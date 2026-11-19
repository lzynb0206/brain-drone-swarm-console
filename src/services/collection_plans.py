"""Selectable acquisition schemes shared by the preview and experiment runner."""

from dataclasses import dataclass, replace

from src.services.hybrid_experiment_protocol import HybridExperimentConfig


COLLECTION_PARADIGMS = (
    ("SSVEP", "SSVEP"),
    ("MI", "MI 运动想象"),
    ("SSVEP+MI混合", "SSVEP＋MI 混合"),
)

REST_SCHEMES = ("rest_open", "rest_closed")


@dataclass(frozen=True)
class CollectionScheme:
    id: str
    title: str
    description: str
    start_label: str


def collection_schemes(paradigm: str) -> tuple[CollectionScheme, ...]:
    resting = (
        CollectionScheme("rest_open", "睁眼静息", "单独记录，到时自动结束", "开始睁眼静息"),
        CollectionScheme("rest_closed", "闭眼静息", "单独记录，结束时提示音", "开始闭眼静息"),
    )
    if paradigm == "SSVEP":
        return (CollectionScheme("formal", "平衡随机采集", "空闲与动作按随机计划采集", "开始自动采集"), *resting)
    if paradigm not in ("MI", "SSVEP+MI混合"):
        raise ValueError("未知采集范式")
    return (
        CollectionScheme("formal", "正式任务采集", "短暂休息后自动开始任务", "开始任务采集"),
        CollectionScheme("pilot", "流程试跑 · 2 组", "验证时序，单独标记试跑数据", "开始流程试跑"),
        *resting,
    )


def hybrid_collection_config(
    base: HybridExperimentConfig, paradigm: str, scheme: str = "formal",
) -> HybridExperimentConfig:
    """Resolve overrides without changing the user's saved formal timings."""
    if paradigm not in ("MI", "SSVEP+MI混合") and not (paradigm == "SSVEP" and scheme in REST_SCHEMES):
        raise ValueError("MI / SSVEP＋MI 混合采集需要对应范式")
    if scheme != "rest" and scheme not in {item.id for item in collection_schemes(paradigm)}:
        raise ValueError("当前范式不支持此采集方案")
    config = replace(base, mode="mi" if paradigm == "MI" else "serial", purpose="formal",
                     include_dedicated_rest=False, idle_trials=0)
    if scheme in (*REST_SCHEMES, "rest"):
        config = replace(config, mode="rest", include_dedicated_rest=True,
                         rest_eye_state={"rest_open": "open", "rest_closed": "closed"}.get(scheme, "both"))
    elif scheme == "pilot":
        config = replace(config, purpose="pilot", repetitions=1, idle_trials=2, include_dedicated_rest=False)
    return config.validate()
