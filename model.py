# Copyright (c) 2022 Armin Fisslthaler <armin@fisslthaler.net>, All rights reserved.
# SPDX-License-Identifier: GPL-3.0

import math
from typing import List
from enum import Enum
from dataclasses import dataclass

@dataclass
class VirtualMachine:
    internal_id: int

    id: int
    memory_used: int
    memory_max: int
    cpu_used: float
    cpu_max: float
    node: str
    name: str
    state: str
    locked: bool


    # TODO:
    # - running_cost()
    # - migration_cost()

    def memory_cost(self):
        if self.state == 'running':
            return self.memory_used
        else:
            # TODO: handle costs of stopped VMs better
            return self.memory_max // 10

    def migration_cost(self):
        if self.state == 'running':
            return (self.memory_used // 1024**2)
        else:
            # TODO: handle costs of stopped VMs better
            # to get deterministic placing *some* cost must be assigned to stopped VMs
            # this is likely because migration_cost is treated as a running cost somewhere
            return self.memory_max // 1024**2 // 10

    def cpu_cost(self):
        if self.state == 'running':
            return math.ceil(self.cpu_used * 100)
        else:
            # TODO: handle costs of stopped VMs better
            # to get deterministic placing *some* cost must be assigned to stopped VMs
            # this is likely because migration_cost is treated as a running cost somewhere
            return 0


    # def cost(self):
    #     if self.state == 'running':
    #         return (self.memory_used // 1024**2)
    #     else:
    #         # TODO: handle costs of stopped VMs better
    #         return self.memory_max // 1024**2 // 10


@dataclass
class Node:
    internal_id: int

    name: str
    memory_used: int
    memory_total: int

    num_cpu: int

    virtual_machines: List[VirtualMachine]

    @property
    def id(self):
        return self.name

