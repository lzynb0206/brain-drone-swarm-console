"""Researcher page shell and section dispatch."""

from __future__ import annotations

from src.views.research_acquisition_view import ResearchAcquisitionViewMixin
from src.views.research_collection_view import ResearchCollectionViewMixin
from src.views.research_configuration_view import ResearchConfigurationViewMixin
from src.widgets.scifi_canvas import BG, RESEARCH_NAV_ITEMS, Canvas


class ResearchViewMixin(
    ResearchCollectionViewMixin,
    ResearchAcquisitionViewMixin,
    ResearchConfigurationViewMixin,
):
    """Dispatch the researcher shell to focused page renderers."""

    def _paint_research(self, c: Canvas) -> None:
        c.fill(BG)
        c.top_nav(self.research_page)
        for name, x, width in RESEARCH_NAV_ITEMS:
            if name == "帮助":
                self._hit(x, 0, width, 56, "openbci-guide")
                continue
            self._hit(x, 0, width, 57, f"research:{name}")
        self._hit(1020, 9, 36, 36, "logout")
        self._hit(1068, 9, 36, 36, "workspace-summary")
        self._hit(1117, 9, 36, 36, "workspace-layout")
        self._hit(1166, 10, 89, 34, "marker-add")
        {
            "采集": self._research_acquisition,
            "LSL收集": self._research_lsl_collector,
            "实验": self._research_replay,
            "范式": self._research_paradigm,
            "算法": self._research_algorithm,
            "控制": self._research_controller,
            "映射": self._research_mapping,
            "数据": self._research_data,
        }[self.research_page](c)


__all__ = ["ResearchViewMixin"]
