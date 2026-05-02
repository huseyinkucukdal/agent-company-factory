"""Loop detection: structural (cycle in (from→to) edges) and semantic
(near-duplicate content)."""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass


def _jaccard(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    sa = set(a.lower().split())
    sb = set(b.lower().split())
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


@dataclass
class LoopDetectorConfig:
    structural_window: int = 12       # last N (from,to) edges per recipient
    structural_threshold: int = 5     # same edge ≥N consecutively → loop
    pingpong_threshold: int = 3       # A↔B repeats
    semantic_window: int = 5
    semantic_similarity: float = 0.9
    semantic_threshold: int = 3


class LoopDetector:
    """Tracks recent traffic per recipient agent and flags loops."""

    def __init__(self, config: LoopDetectorConfig | None = None) -> None:
        self._cfg = config or LoopDetectorConfig()
        self._edges: dict[str, deque[tuple[str | None, str]]] = {}
        self._pair_edges: dict[tuple[str, str], deque[tuple[str | None, str]]] = {}
        self._contents: dict[str, deque[str]] = {}
        self._lock = threading.Lock()

    # ----- structural

    def _record_edge(
        self, recipient: str, edge: tuple[str | None, str],
    ) -> bool:
        win = self._edges.setdefault(
            recipient, deque(maxlen=self._cfg.structural_window),
        )
        win.append(edge)
        # consecutive same edges (recipient gets pestered by same sender)
        if len(win) >= self._cfg.structural_threshold:
            tail = list(win)[-self._cfg.structural_threshold :]
            if all(e == edge for e in tail):
                return True
        # ping-pong A↔B — keyed by canonical pair so both directions land
        # in one window.
        if edge[0] is not None:
            pair = tuple(sorted([edge[0], edge[1]]))
            assert len(pair) == 2
            pair_key: tuple[str, str] = (pair[0], pair[1])
            pair_win = self._pair_edges.setdefault(
                pair_key, deque(maxlen=self._cfg.structural_window),
            )
            pair_win.append(edge)
            if len(pair_win) >= self._cfg.pingpong_threshold * 2:
                tail = list(pair_win)[-self._cfg.pingpong_threshold * 2 :]
                a, b = tail[0], tail[1]
                if a != b and all(
                    tail[i] == (a if i % 2 == 0 else b)
                    for i in range(len(tail))
                ):
                    return True
        return False

    # ----- semantic

    def _record_content(self, recipient: str, content: str) -> bool:
        win = self._contents.setdefault(
            recipient, deque(maxlen=self._cfg.semantic_window),
        )
        similar = sum(
            1 for prev in win
            if _jaccard(prev, content) >= self._cfg.semantic_similarity
        )
        win.append(content)
        return similar >= (self._cfg.semantic_threshold - 1)

    # ----- public

    def check(
        self, *, from_agent: str | None, to_agent: str, content: str,
    ) -> tuple[bool, str | None]:
        with self._lock:
            if self._record_edge(to_agent, (from_agent, to_agent)):
                return True, "structural"
            if self._record_content(to_agent, content):
                return True, "semantic"
            return False, None


__all__ = ["LoopDetector", "LoopDetectorConfig"]
