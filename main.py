# Copyright (c) 2022 Armin Fisslthaler <armin@fisslthaler.net>, All rights reserved.
# SPDX-License-Identifier: GPL-3.0

import math
from proxmoxer import ProxmoxAPI
from proxmoxer.core import ResourceException as ProxmoxerResourceException
from pprint import pprint
import time
import logging
from config import Config
import urllib3
import sys

from ars_model import ARSModel
from model import *
from connections import pve as proxmox

def build_migrations(old, new):
    vmid_vm_map = {vm.id: vm for node in old for vm in node.virtual_machines}
    old_vm_node_map = {(vm.id, node.name) for node in old for vm in node.virtual_machines}
    new_vm_node_map = {(vm.id, node.name) for node in new for vm in node.virtual_machines}
    migrations = new_vm_node_map - old_vm_node_map

    return [ (vmid_vm_map[vmid], dst_node) for vmid, dst_node in migrations ]

def main():
    config = Config.from_file('ars.cfg')
    if not config.general.verify_ssl:
        print("WARNING:  Unverified HTTPS request are being made. Adding certificate verification is strongly advised.")

    pve = ProxmoxAPI(host=config.general.host, user=config.general.user,
                     password=config.general.password, verify_ssl=config.general.verify_ssl)

    # logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    # logger.setLevel(logging.INFO)

    # fetch current vm-to-host mappings
    state = proxmox.fetch_current_state(pve)

    ars = ARSModel(state, config)

    # calculate an optimal state based based on that
    new_state = ars.calculate_balanced_state()

    migrations = build_migrations(state, new_state)

    print()
    print("state")
    total_memory = sum([vm.memory_cost() for node in state for vm in node.virtual_machines])
    total_cpu = sum([vm.cpu_cost() for node in state for vm in node.virtual_machines])
    print("node", "memory", "cpu", "num_vms", sep='\t')
    for node in state:
        node_vm_memory = sum([vm.memory_cost() for vm in node.virtual_machines])
        node_vm_cpu = sum([vm.cpu_cost() for vm in node.virtual_machines])
        print(node.name, node_vm_memory/total_memory, node_vm_memory//config.model.memory_precision, node_vm_cpu/total_cpu, node_vm_cpu, len(node.virtual_machines), sep='\t')
        continue
        for vm in node.virtual_machines:
            print('\t', vm)

    print()

    print("new_state")
    total_memory = sum([vm.memory_cost() for node in new_state for vm in node.virtual_machines])
    total_cpu = sum([vm.cpu_cost() for node in new_state for vm in node.virtual_machines])
    print("node", "memory", "cpu", "num_vms", sep='\t')
    for node in new_state:
        node_vm_memory = sum([vm.memory_cost() for vm in node.virtual_machines])
        node_vm_cpu = sum([vm.cpu_cost() for vm in node.virtual_machines])
        print(node.name, node_vm_memory/total_memory, node_vm_memory//config.model.memory_precision, node_vm_cpu/total_cpu, node_vm_cpu, len(node.virtual_machines), sep='\t')
        continue
        for vm in node.virtual_machines:
            print('\t', vm)

    # migrations = sort_migrations_by_best(migrations)
    #
    migrations = sorted(migrations, key=lambda x: x[0].migration_cost())
    print()
    # pprint(migrations)
    print("len(migrations)", len(migrations))
    print("cost(migrations)", sum([migration[0].migration_cost() for migration in migrations]))

    migration_cost = sum([migration[0].migration_cost() for migration in migrations])
    if migration_cost < 30000:
        print("skipped, below threshold")
        sys.exit(0)
    # sys.exit(1)

    proxmox.realize_migrations(logger, pve, migrations, cfg=config)
    print("finished")

if __name__ == '__main__':
    urllib3.disable_warnings() # disable ssl warnings, we warn elsewhere
    main()

