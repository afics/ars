# Copyright (c) 2022 Armin Fisslthaler <armin@fisslthaler.net>, All rights reserved.
# SPDX-License-Identifier: GPL-3.0

import sys
import config

from copy import copy

from itertools import combinations

from typing import Set

import math

from ortools.sat.python import cp_model

class ObjectivePrinter(cp_model.CpSolverSolutionCallback):
    """Print intermediate solutions."""

    def __init__(self, solver, migration_cost, node_cpu_cost_distances, node_mem_cost_distances):
        cp_model.CpSolverSolutionCallback.__init__(self)
        self.__solver = solver
        self.__migration_cost = migration_cost
        self.__node_cpu_cost_distances = node_cpu_cost_distances
        self.__node_mem_cost_distances = node_mem_cost_distances

        self.__solution_count = 0

    def on_solution_callback(self):
        migration_cost = self.Value(self.__migration_cost)

        print('Solution %i, time = %f s, objective = %i, migration_cost = %i' %
              (self.__solution_count, self.WallTime(), self.ObjectiveValue(), migration_cost))
        self.__solution_count += 1

        for i, (cpu_c, mem_c) in enumerate(zip(self.__node_cpu_cost_distances, self.__node_mem_cost_distances)):
            cpu_c = self.Value(cpu_c)
            mem_c = self.Value(mem_c)

            cpu_c = math.sqrt(cpu_c)
            mem_c = math.sqrt(mem_c)

            print('\t', i, mem_c, cpu_c, sep='\t')

class ARSModel:
    def __init__(self, nodes, cfg):
        self.nodes = nodes
        self.cfg = cfg

    @property
    def all_nodes(self):
        for node in self.nodes:
            yield (node.internal_id, node)

    def all_nodes_except(self, ids: Set):
        for node in self.nodes:
            if node.id not in ids:
                yield (node.internal_id, node)

    @property
    def maintenance_nodes(self):
        for _, node in self.all_nodes:
            if node.id in self.cfg.maintenance.nodes:
                yield (node.internal_id, node)

    def nodes_with_connector_ids(self, ids: Set):
        for node in self.nodes:
            if node.id in ids:
                yield (node.internal_id, node)

    @property
    def all_vms(self):
        for node in self.nodes:
            for vm in node.virtual_machines:
                yield (vm.internal_id, vm)

    @property
    def num_vms(self):
        return sum([len(node.virtual_machines) for _, node in self.all_nodes])

    def vms_with_connector_ids(self, ids: Set):
        for node in self.nodes:
            for vm in node.virtual_machines:
                if vm.id in ids:
                    yield (vm.internal_id, vm)

    @property
    def total_memory_costs(self):
        return sum(vm.memory_cost() // self.cfg.model.memory_precision for _, vm in self.all_vms)

    @property
    def total_cpu_costs(self):
        return sum(vm.cpu_cost() for _, vm in self.all_vms)

    @property
    def total_migration_costs(self):
        # TODO: handle memory_precision
        return sum(vm.migration_cost() for _, vm in self.all_vms)

    @property
    def total_usable_cluster_cpu(self):
        return sum(node.num_cpu * 100 for _, node in self.all_nodes if node.id not in self.cfg.maintenance.nodes)

    @property
    def total_usable_cluster_memory(self):
        return sum(node.memory_total // self.cfg.model.memory_precision for _, node in self.all_nodes if node.id not in self.cfg.maintenance.nodes)


    def calculate_balanced_state(self):
        """Solve the assignment problem."""


        model = cp_model.CpModel()

        ## problem definition

        # x_{node, vm} = 1 if vm is assigned to node
        x = {}
        for node_id, node in self.all_nodes:
            for vm_id, vm in self.all_vms:
                x[node_id, vm_id] = model.NewBoolVar(f'x[{node_id},{vm_id}]')

        # p_{node, vm} = vm_cost -> migration penalty, keep VMs where they are if they're costly to move (sticky map)
        p = {}
        for node_id, node in self.all_nodes:
            for vm_id, vm in self.all_vms:
                p[node_id, vm_id] = model.NewIntVar(0, self.total_migration_costs, f'p[{node_id},{vm_id}]')
                if vm.node == node.name: # vm is running on node, so keeping it there does not cost anything
                    model.Add(p[node_id, vm_id] == 0)
                else: # migration is defined as the same cost as running the VM
                    model.Add(p[node_id, vm_id] == vm.migration_cost())

        ## system constraints
        # each VM is assigned to exactly one node
        for vm_id, _ in self.all_vms:
            model.Add(sum(x[node_id, vm_id] for node_id, _ in self.all_nodes) == 1)

        # each node has a maximum memory capacity
        for node_id, node in self.all_nodes:
            # TODO: make me prettier
            model.Add(sum( x[node_id, vm_id] * (vm.memory_used // self.cfg.model.memory_precision) for vm_id, vm in self.all_vms) <= (node.memory_total // self.cfg.model.memory_precision))

        # pin locked VMs to their current nodes
        for vm_id, vm in self.all_vms:
            if vm.locked:
                inverted_nodes = self.all_nodes_except({vm.node})
                node_id, _ = next(self.nodes_with_connector_ids({vm.node}))
                model.Add(x[node_id, vm_id] == 1)

        ## user constraints
        # exclude administrator disabled nodes
        for node_id, _ in self.maintenance_nodes:
            # no VMs must run on this node
            model.Add(sum(x[node_id, vm_id] for vm_id, _ in self.all_vms) == 0)


        # vm-to-vm affinity
        for rule in self.cfg.affinity_rules.vm_to_vm:
            if not rule.enabled: # skip disabled rules
                continue

            # anti affinity
            if rule.type_ == config.Vm2VmAffinityType.KEEP_APART and rule.enabled:
                rule_vms = self.vms_with_connector_ids(rule.virtual_machines)

                # iterate over all possible combinations of the listed VMs
                for (vm_a_id, _), (vm_b_id, _) in combinations(rule_vms, 2):
                    for node_id, _ in self.all_nodes:
                        model.Add((x[node_id, vm_a_id] + x[node_id, vm_b_id]) < 2)

            # affinity
            elif rule.type_ == config.Vm2VmAffinityType.KEEP_TOGETHER and rule.enabled:
                rule_vms = self.vms_with_connector_ids(rule.virtual_machines)

                # iterate over all possible combinations of the listed VMs
                for (vm_a_id, vm_a), (vm_b_id, vm_b) in combinations(rule_vms, 2):
                    node_rules = []
                    for node_id, _ in self.all_nodes:
                        # check whether a node holds both VMs
                        vms_together_on_node = model.NewBoolVar(f'node[{node_id}]_vm[{vm_a.id},{vm_b.id}]_presence')
                        model.Add((x[node_id, vm_a_id] + x[node_id, vm_b_id]) == 2).OnlyEnforceIf(vms_together_on_node)
                        model.Add((x[node_id, vm_a_id] + x[node_id, vm_b_id]) != 2).OnlyEnforceIf(vms_together_on_node.Not())
                        node_rules.append(vms_together_on_node)

                    # only one node might hold both VMs
                    model.Add(sum(node_rules) == 1)

        # vm-to-host anti affinity
        for rule in self.cfg.affinity_rules.vm_to_host:
            if not rule.enabled: # skip disabled rules
                continue

            if rule.type_ == config.Vm2HostAffinityType.RUN_ELSEWHERE:
                for vm_id, _ in self.vms_with_connector_ids(rule.virtual_machines):
                    for node_id, _ in self.nodes_with_connector_ids(rule.nodes):
                        model.Add(x[node_id, vm_id] < 1)

            elif rule.type_ == config.Vm2HostAffinityType.RUN_HERE:
                inverted_nodes = self.all_nodes_except(rule.nodes)
                for vm_id, _ in self.vms_with_connector_ids(rule.virtual_machines):
                    for node_id, _ in inverted_nodes:
                        model.Add(x[node_id, vm_id] < 1)

        ## Objective
        # TODO: validated constraints before or tools to give meaning full error messages
        # minimize cost per node to total_cost/node_count

        node_cpu_cost_distances = []
        node_mem_cost_distances = []
        for node_id, node in self.all_nodes:
            mem_c = model.NewIntVar(0, self.total_memory_costs, f'total_memory_costs_of_node_{node_id}')
            model.Add(mem_c == sum((vm.memory_cost() // self.cfg.model.memory_precision) * x[node_id, vm_id] for vm_id, vm in self.all_vms))

            cpu_c = model.NewIntVar(0, self.total_cpu_costs, f'total_cpu_costs_of_node_{node_id}')
            model.Add(cpu_c == sum((vm.cpu_cost()) * x[node_id, vm_id] for vm_id, vm in self.all_vms))

            # TODO: FIXME: STARTHERE
            # make calculation clearer
            # calculate cluster fraction
            # calculate node fraction from that

            node_cpu_fraction = (node.num_cpu * 100) / self.total_usable_cluster_cpu
            node_mem_fraction = (node.memory_total // self.cfg.model.memory_precision) / self.total_usable_cluster_memory

            node_cpu_target_fraction = math.ceil(self.total_cpu_costs * node_cpu_fraction)

            node_mem_target_fraction = math.ceil(self.total_memory_costs * node_mem_fraction)

            cpu_target_fraction_distance = model.NewIntVar(-1-self.total_cpu_costs, self.total_cpu_costs, f'total_cpu_costs_of_node_{node_id}')
            model.Add(cpu_target_fraction_distance == cpu_c - node_cpu_target_fraction)

            mem_target_fraction_distance = model.NewIntVar(-1-self.total_memory_costs, self.total_memory_costs, f'total_mem_costs_of_node_{node_id}')
            model.Add(mem_target_fraction_distance == mem_c - node_mem_target_fraction)

            cpu_target_fraction_distance_squared = model.NewIntVar(0, self.total_cpu_costs**2, f'total_cpu_costs_of_node_{node_id}')
            model.AddMultiplicationEquality(cpu_target_fraction_distance_squared, cpu_target_fraction_distance, cpu_target_fraction_distance)

            mem_target_fraction_distance_squared = model.NewIntVar(0, self.total_memory_costs**2, f'total_mem_costs_of_node_{node_id}')
            model.AddMultiplicationEquality(mem_target_fraction_distance_squared, mem_target_fraction_distance, mem_target_fraction_distance)

            node_cpu_cost_distances.append(cpu_target_fraction_distance_squared)
            node_mem_cost_distances.append(mem_target_fraction_distance_squared)

            print("node", "type", "lfrac", "cfrac" ,sep='\t')
            print(node.name, "cpu", node_cpu_target_fraction, node_cpu_fraction, sep='\t')
            print(node.name, "mem", node_mem_target_fraction, node_mem_fraction, sep='\t')
            print()

        # calculate migration penalty
        per_vm_migration_costs = [] # migration penalties
        for node_id, _ in self.all_nodes:
            for vm_id, vm in self.all_vms:
                vm_p = model.NewIntVar(0, self.total_migration_costs, f'memory_penalty_of_vm_{vm_id}_to_{node_id}')
                model.AddMultiplicationEquality(vm_p, [x[node_id, vm_id], p[node_id, vm_id]])
                per_vm_migration_costs.append(vm_p)

        per_node_memory_costs = []
        for node_id, node in self.all_nodes:
            c = model.NewIntVar(0, self.total_memory_costs, f'total_memory_costs_of_node_{node_id}')
            model.Add(c == sum((vm.memory_cost() // self.cfg.model.memory_precision) * x[node_id, vm_id] for vm_id, vm in self.all_vms))

            # (total node costs)^2
            cs = model.NewIntVar(0, self.total_memory_costs**2, f'squared_total_costs_of_node_{node_id}')
            model.AddMultiplicationEquality(cs, c, c)

            per_node_memory_costs.append(cs)

        per_node_cpu_costs = []
        for node_id, node in self.all_nodes:
            c = model.NewIntVar(0, self.total_cpu_costs, f'total_cpu_costs_of_node_{node_id}')
            model.Add(c == sum((vm.cpu_cost()) * x[node_id, vm_id] for vm_id, vm in self.all_vms))

            # (total node costs)^2
            cs = model.NewIntVar(0, self.total_cpu_costs**2, f'squared_total_costs_of_node_{node_id}')
            model.AddMultiplicationEquality(cs, c, c)

            per_node_cpu_costs.append(cs)



        # objective

        # migration_cost
        migration_cost = model.NewIntVar(0, self.total_migration_costs**2*self.num_vms, 'obj_migration_cost')
        model.Add(migration_cost == sum(per_vm_migration_costs))


        obj = model.NewIntVar(0, self.total_memory_costs**2*self.num_vms + self.total_migration_costs, 'obj')
        model.Add(obj == sum(node_cpu_cost_distances)*5000000 + sum(node_mem_cost_distances)*5000 + migration_cost)

        model.Minimize(obj)

        # Solve and print out the solution.
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.cfg.solver.max_time_in_seconds
        solver.parameters.num_search_workers = self.cfg.solver.num_search_workers

        objective_printer = ObjectivePrinter(solver, migration_cost, node_cpu_cost_distances, node_mem_cost_distances)
        status = solver.Solve(model, objective_printer)
        print()
        print(solver.ResponseStats())

        if status == cp_model.OPTIMAL or cp_model.FEASIBLE:
            if status == cp_model.INFEASIBLE:
                print("INFEASIBLE :(")
                sys.exit(1)
            elif status == cp_model.OPTIMAL:
                print("OPTIMAL")
            else:
                print("FEASIBLE")

            print()

            result = []

            for node_id, node in self.all_nodes:
                node_ = copy(node)
                node_.virtual_machines = []
                for vm_id, vm in self.all_vms:
                    if solver.BooleanValue(x[node_id, vm_id]): # vm has been placed on node
                        node_.virtual_machines.append(vm)

                result.append(node_)

            return result

