"""Researcher page shell and section dispatch."""

from __future__ import annotations

from PySide6.QtCore import QRectF

from src.views.research_acquisition_view import ResearchAcquisitionViewMixin
from src.views.research_collection_view import ResearchCollectionViewMixin
from src.views.research_configuration_view import ResearchConfigurationViewMixin
from src.views.research_single_target_view import ResearchSingleTargetViewMixin
from src.widgets.scifi_canvas import RESEARCH_BG, RESEARCH_NAV_ITEMS, Canvas


RESEARCH_COMBINED_PAGE = "范式算法"
RESEARCH_COMBINED_MAX_SCROLL = 520.0


class ResearchViewMixin(
    ResearchCollectionViewMixin,
    ResearchAcquisitionViewMixin,
    ResearchConfigurationViewMixin,
    ResearchSingleTargetViewMixin,
):
    """Dispatch the researcher shell to focused page renderers."""

    def _paint_research(self, c: Canvas) -> None:
        c.use_research_theme()
        c.fill(RESEARCH_BG)
        current_page = {
            "实验": "CSV回放",
            "范式": RESEARCH_COMBINED_PAGE,
            "算法": RESEARCH_COMBINED_PAGE,
        }.get(self.research_page, self.research_page)
        c.top_nav(current_page)
        for name, x, width in RESEARCH_NAV_ITEMS:
            if name == "SSVEP测试":
                self._hit(x, 0, width, 56, "ssvep-command-test")
                continue
            if name == "帮助":
                self._hit(x, 0, width, 56, "openbci-guide")
                continue
            self._hit(x, 0, width, 57, f"research:{name}")
        self._hit(1020, 9, 36, 36, "logout")
        self._hit(1068, 9, 36, 36, "workspace-summary")
        self._hit(1117, 9, 36, 36, "workspace-layout")
        self._hit(1166, 10, 89, 34, "marker-add")
        renderers = {
            "采集": self._research_acquisition,
            "LSL收集": self._research_lsl_collector,
            "CSV回放": self._research_replay,
            "实验": self._research_replay,
            RESEARCH_COMBINED_PAGE: self._research_paradigm_algorithm,
            "范式": self._research_paradigm_algorithm,
            "算法": self._research_paradigm_algorithm,
            "数据采集": self._research_data_collection,
            "数据": self._research_data,
            "单目标校准": self._research_single_target_calibration,
        }
        if current_page != RESEARCH_COMBINED_PAGE:
            renderers[self.research_page](c)
            return

        scroll_offset = max(
            0.0,
            min(
                RESEARCH_COMBINED_MAX_SCROLL,
                float(getattr(self, "research_scroll_offset", 0.0)),
            ),
        )
        c.p.save()
        try:
            c.p.setClipRect(QRectF(0, 56, 1280, 664))
            c.p.translate(0, -scroll_offset)
            self._research_content_scrolling = True
            self._research_section_y_offset = 0.0
            self._research_paradigm_algorithm(c)
        finally:
            self._research_content_scrolling = False
            self._research_section_y_offset = 0.0
            c.p.restore()
        self._research_scrollbar(c, scroll_offset)

    def _research_scrollbar(self, c: Canvas, offset: float) -> None:
        track_y, track_h = 72.0, 612.0
        thumb_h = 184.0
        travel = track_h - thumb_h
        ratio = offset / RESEARCH_COMBINED_MAX_SCROLL
        c.rect(1268, track_y, 5, track_h, "#e1e7ee", None, 2.5)
        c.rect(1268, track_y + travel * ratio, 5, thumb_h, "#8aa4c4", None, 2.5)


__all__ = [
    "RESEARCH_COMBINED_MAX_SCROLL",
    "RESEARCH_COMBINED_PAGE",
    "ResearchViewMixin",
]
