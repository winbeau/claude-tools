from . import (  # noqa: F401
    bit,
    buaa,
    fudan,
    hust,
    nankai,
    nju,
    nwpu,
    pku,
    seu,
    shtech,
    sjtu,
    sysu,
    tju,
    tsinghua,
    uestc,
    ustc,
    xidian,
    xjtu,
    zju,
)
from .base import REGISTRY, SchoolAdapter  # noqa: F401


def get_adapter(school_code: str) -> SchoolAdapter:
    if school_code not in REGISTRY:
        raise KeyError(
            f"No adapter for school '{school_code}'. Registered: {sorted(REGISTRY)}"
        )
    return REGISTRY[school_code]()
