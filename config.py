# Copyright (c) 2022 Armin Fisslthaler <armin@fisslthaler.net>, All rights reserved.
# SPDX-License-Identifier: GPL-3.0

import enum
from typing import List, Set, Optional
from dataclasses import dataclass
from serde import serde, field
from serde.toml import from_toml

class Vm2VmAffinityType(enum.Enum):
    KEEP_TOGETHER = "keep-together"
    KEEP_APART = "keep-apart"

class Vm2HostAffinityType(enum.Enum):
    RUN_HERE = "run-here"
    RUN_ELSEWHERE = "run-elsewhere"

@serde
@dataclass
class General:
    host: str
    user: str
    password: str
    verify_ssl: Optional[bool] = True

@serde
@dataclass
class Model:
    memory_precision: int = 1024**2

@serde
@dataclass
class Solver:
    max_time_in_seconds: int = 10
    num_search_workers: int = 1

@serde
@dataclass
class Migration:
    max_migrations_per_host: int = 3


@serde
@dataclass
class Maintenance:
    nodes: Optional[Set[int]] = field(default_factory=set)


@serde
@dataclass
class Vm2VmAffinityRule:
    name: Optional[str]
    comment: Optional[str]
    enabled: bool = True
    type_: Vm2VmAffinityType = field(rename="type", default=Vm2VmAffinityType.KEEP_APART)
    virtual_machines: Optional[Set[int]] = field(rename="vms", default_factory=set)

@serde
@dataclass
class Vm2HostAffinityRule:
    name: Optional[str]
    comment: Optional[str]
    nodes: Set[str]
    enabled: bool = True
    type_: Vm2HostAffinityType = field(rename="type", default=Vm2HostAffinityType.RUN_HERE)
    virtual_machines: Optional[Set[int]] = field(rename="vms", default_factory=set)

@serde
@dataclass
class AffinityRules:
    vm_to_vm: List[Vm2VmAffinityRule] = field(rename="vm-to-vm", default_factory=list)
    vm_to_host: List[Vm2HostAffinityRule] = field(rename="vm-to-host", default_factory=list)

@serde
@dataclass
class Config:
    general: General
    model: Model
    solver: Solver
    migration: Migration
    maintenance: Maintenance = field(rename="maintenance", default=Maintenance())
    affinity_rules: AffinityRules = field(rename="affinity-rules", default=AffinityRules())

    @staticmethod
    def from_file(file_):
        with open(file_, 'r') as f:
            return from_toml(Config, f.read())

