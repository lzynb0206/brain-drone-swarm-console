"""Researcher page shell and section dispatch."""

from __future__ import annotations

from PySide6.QtCore import QRectF

from src.views.research_acquisition_view import ResearchAcquisitionViewMixin
from src.views.research_collection_view import ResearchCollectionViewMixin
from src.views.research_configuration_view import ResearchConfigurationViewMixin
from src.widgets.scifi_canvas import RESEARCH_BG, RESEARCH_NAV_ITEMS, Canvas


class ResearchViewMixin(
    ResearchCollectionViewMixin,
    ResearchAcquisitionViewMixin,
    ResearchConfigurationViewMixin,
):
    """Dispatch the researcher shell to focused page renderers."""

    def _paint_research(self, c: Canvas) -> None:
        c.use_research_theme()
        c.fill(RESEARCH_BG)
        current_page = {
            "实验": "CSV回放",
        }.get(self.research_page, self.research_page)
        c.top_nav(current_page)
        for name, x, width in RESEARCH_NAV_ITEMS:
            if name == "SSVEP测试":
                self._hit(x, 0, width, 56, "ssvep-command-test")
                continue
            self._hit(x, 0, width, 57, f"research:{name}")
        self._hit(1154, 10, 101, 34, "logout")
        renderers = {
            "采集": self._research_acquisition,
            "LSL收集": self._research_lsl_collector,
            "CSV回放": self._research_replay,
            "实验": self._research_replay,
            "数据采集": self._research_data_collection,
            "数据分析": self._research_data,
        }
        renderer = renderers[self.research_page]
        if current_page == "数据分析":
            max_scroll = self._research_data_max_scroll()
        else:
            renderer(c)
            return

        scroll_offset = max(
            0.0,
            min(
                max_scroll,
                float(getattr(self, "research_scroll_offset", 0.0)),
            ),
        )
        c.p.save()
        try:
            c.p.setClipRect(QRectF(0, 56, 1280, 664))
            c.p.translate(0, -scroll_offset)
            self._research_effective_scroll_offset = scroll_offset
            self._research_content_scrolling = True
            renderer(c)
        finally:
            self._research_content_scrolling = False
            self._research_effective_scroll_offset = 0.0
            c.p.restore()
        if max_scroll > 0:
            self._research_scrollbar(c, scroll_offset, max_scroll)

    def _research_scrollbar(self, c: Canvas, offset: float, max_scroll: float) -> None:
        track_y, track_h = 72.0, 612.0
        content_height = track_h + max_scroll
        thumb_h = max(80.0, track_h * track_h / content_height)
        travel = track_h - thumb_h
        ratio = offset / max_scroll
        c.rect(1268, track_y, 5, track_h, "#e5e5e5", None, 2.5)
        c.rect(1268, track_y + travel * ratio, 5, thumb_h, "#8c8c8c", None, 2.5)


__all__ = [
    "ResearchViewMixin",
]
