# Copyright (c) 2022 Armin Fisslthaler <armin@fisslthaler.net>, All rights reserved.
# SPDX-License-Identifier: GPL-3.0

import math
from proxmoxer.core import ResourceException as ProxmoxerResourceException
import time

from model import VirtualMachine, Node

def wait_for_tasks(proxmox, migration_nodes, running):
    while True:
        for task in proxmox.cluster.tasks.get():
            if "endtime" in task and task["upid"] in running:
                print(sorted(task.items()))
                if task["status"] != "OK": # retry failed migrations
                    vm, dst_node = running[task["upid"]]
                    del running[task["upid"]]

                    new_task = proxmox.nodes(vm.node).qemu(vm.id).migrate.post(**{
                        "target": dst_node,
                        "online": 1,
                        "with-local-disks": 1,
                    })

                    running[new_task] = (vm, dst_node)
                else: # otherwise mark as done
                    try:
                        vm, dst_node = running[task["upid"]]

                        # task has stopped, remove from migration node load monitoring
                        migration_nodes[vm.node] -= 1
                        migration_nodes[dst_node] -= 1

                        del running[task["upid"]]
                    except KeyError:
                        pass

                    return # exit after a task terminates to schedule a new one

        time.sleep(1)


def realize_migrations(logger, proxmox, migrations, cfg):
    # TODO: handle failed migrations

    MAX_MIGRATIONS_PER_HOST = cfg.migration.max_migrations_per_host

    running = {}
    src_nodes = {vm.node for vm, _ in migrations}
    dst_nodes = {dst_node for _, dst_node in migrations}
    migration_nodes = { node: 0 for node in src_nodes | dst_nodes }

    while migrations:
        for i, (vm, dst_node) in enumerate(migrations):

            src_node = vm.node
            if migration_nodes[src_node] >= MAX_MIGRATIONS_PER_HOST:
                logger.debug(
                    "Postponing migration of VM {} , because src node {} is busy".format(vm.id, src_node)
                )
                continue
            if migration_nodes[dst_node] >= MAX_MIGRATIONS_PER_HOST:
                logger.debug(
                    "Postponing migration of VM {} , because dst node {} is busy".format(vm.id, dst_node)
                )
                continue

            logger.info(
                "Migrating VM {}='{}' from {} to {}.".format(vm.id, vm.name, vm.node, dst_node)
            )

            task = proxmox.nodes(vm.node).qemu(vm.id).migrate.post(**{
                "target": dst_node,
                "online": 1,
                "with-local-disks": 1,
            })

            # increase node task count for migration on source and destination node
            migration_nodes[vm.node] += 1
            migration_nodes[dst_node] += 1

            running[task] = (vm, dst_node)
            del migrations[i]
            break
        else:
            time.sleep(1)
            wait_for_tasks(proxmox, migration_nodes, running)


    while len(running) > 0:
        wait_for_tasks(proxmox, migration_nodes, running)

def fetch_current_state(pve):
    nodes = []

    internal_vmid = 0

    for internal_node_id, node in enumerate(sorted(pve.nodes.get(), key=lambda n: n['node'])):
        virtual_machines = []

        try:
            raw_vms = sorted(pve.nodes(node['node']).qemu.get(full=1), key=lambda v: v['vmid'])
        except ProxmoxerResourceException as e:
            print('node unavailable {!r}'.format(e))
            continue


        for vm in raw_vms:
            if vm['status'] != 'running':
                cpu = 0
                mem = 0
            else:
                rrddata = sorted(pve.nodes(node['node']).qemu(vm['vmid']).rrddata.get(timeframe='hour', cf='MAX'), key=lambda x: x['time'], reverse=True)

                cpu, mem = 0, 0

                for data in rrddata:
                    if 'cpu' in data and 'mem' in data:
                        cpu = data['cpu']
                        mem = math.ceil(data['mem'])
                        break

            virtual_machines.append(VirtualMachine(
                internal_id = internal_vmid,
                id=vm['vmid'],
                name=vm['name'],
                state=vm['status'],
                locked='lock' in vm,

                node=node['node'],

                memory_used=mem,
                memory_max=vm['maxmem'],

                cpu_used=cpu,
                cpu_max=vm['cpus'],
            ))

            internal_vmid += 1

        nodes.append(Node(
            internal_id=internal_node_id,
            name=node["node"],
            memory_used=node["mem"],
            memory_total=node["maxmem"],
            num_cpu=node["maxcpu"],
            virtual_machines=virtual_machines,
        ))

    return nodes

