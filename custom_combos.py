"""Custom Combos — user-defined model fallback chains via API.

Users can create named "combos" that define custom fallback chains
through the dashboard or API. Combos override the default models.yaml
fallback order for a given model name.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

from config import ModelFallback, ModelConfig

logger = logging.getLogger(__name__)

COMBOS_FILE = Path(__file__).parent / "data" / "combos.json"


@dataclass
class ComboEntry:
    provider: str
    model: str
    weight: int = 1  # Higher = more preferred


@dataclass
class Combo:
    name: str
    description: str = ""
    entries: list[ComboEntry] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    created_by: str = "user"

    def to_model_config(self) -> ModelConfig:
        """Convert to a ModelConfig with sorted fallbacks (by weight desc)."""
        sorted_entries = sorted(self.entries, key=lambda e: e.weight, reverse=True)
        return ModelConfig(
            unified_name=self.name,
            fallbacks=[ModelFallback(provider=e.provider, model=e.model) for e in sorted_entries],
        )


class ComboManager:
    """Manages custom model combos with JSON persistence."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._combos: dict[str, Combo] = {}
        self._load()

    def _load(self) -> None:
        if COMBOS_FILE.exists():
            try:
                with open(COMBOS_FILE) as f:
                    data = json.load(f)
                for name, combo_data in data.get("combos", {}).items():
                    entries = [
                        ComboEntry(
                            provider=e["provider"],
                            model=e["model"],
                            weight=e.get("weight", 1),
                        )
                        for e in combo_data.get("entries", [])
                    ]
                    self._combos[name] = Combo(
                        name=name,
                        description=combo_data.get("description", ""),
                        entries=entries,
                        created_at=combo_data.get("created_at", 0),
                        updated_at=combo_data.get("updated_at", 0),
                    )
                logger.info("Loaded %d custom combos", len(self._combos))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Could not load combos file: %s", e)

    def _save(self) -> None:
        COMBOS_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = {"combos": {}}
            for name, combo in self._combos.items():
                data["combos"][name] = {
                    "description": combo.description,
                    "entries": [
                        {"provider": e.provider, "model": e.model, "weight": e.weight}
                        for e in combo.entries
                    ],
                    "created_at": combo.created_at,
                    "updated_at": combo.updated_at,
                }
            with open(COMBOS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            logger.warning("Could not save combos file: %s", e)

    def create_combo(
        self,
        name: str,
        entries: list[dict[str, Any]],
        description: str = "",
    ) -> Combo:
        """Create a new custom combo."""
        with self._lock:
            combo_entries = [
                ComboEntry(
                    provider=e["provider"],
                    model=e["model"],
                    weight=e.get("weight", 1),
                )
                for e in entries
            ]
            now = time.time()
            combo = Combo(
                name=name,
                description=description,
                entries=combo_entries,
                created_at=now,
                updated_at=now,
            )
            self._combos[name] = combo
            self._save()
            logger.info("Created combo '%s' with %d entries", name, len(combo_entries))
            return combo

    def update_combo(
        self,
        name: str,
        entries: list[dict[str, Any]] | None = None,
        description: str | None = None,
    ) -> Combo | None:
        """Update an existing combo."""
        with self._lock:
            combo = self._combos.get(name)
            if not combo:
                return None

            if entries is not None:
                combo.entries = [
                    ComboEntry(
                        provider=e["provider"],
                        model=e["model"],
                        weight=e.get("weight", 1),
                    )
                    for e in entries
                ]
            if description is not None:
                combo.description = description
            combo.updated_at = time.time()
            self._save()
            return combo

    def delete_combo(self, name: str) -> bool:
        """Delete a combo by name."""
        with self._lock:
            if name not in self._combos:
                return False
            del self._combos[name]
            self._save()
            logger.info("Deleted combo '%s'", name)
            return True

    def get_combo(self, name: str) -> Combo | None:
        return self._combos.get(name)

    def list_combos(self) -> list[dict[str, Any]]:
        """List all combos with metadata."""
        return [
            {
                "name": c.name,
                "description": c.description,
                "entries": [
                    {"provider": e.provider, "model": e.model, "weight": e.weight}
                    for e in c.entries
                ],
                "created_at": c.created_at,
                "updated_at": c.updated_at,
            }
            for c in sorted(self._combos.values(), key=lambda c: c.name)
        ]

    def merge_into_config(self, models: dict[str, ModelConfig]) -> int:
        """Merge combos into the models dict. Returns number of combos merged."""
        merged = 0
        for name, combo in self._combos.items():
            models[name] = combo.to_model_config()
            merged += 1
        if merged:
            logger.info("Merged %d custom combos into model config", merged)
        return merged


# Global singleton
combo_manager = ComboManager()
