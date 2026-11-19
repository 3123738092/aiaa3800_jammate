"""Independent musical controls for the accompaniment arranger."""
from dataclasses import dataclass, fields


TEXTURE_CHOICES = {
    "scale": ("default", "pentatonic", "blues"),
    "drums": ("default", "sparse", "steady", "offbeat"),
    "bass": ("default", "drone", "walking", "syncopated"),
    "keys": ("default", "arpeggio", "staccato", "open"),
}


@dataclass(frozen=True)
class ArrangementTexture:
    scale: str = "default"
    drums: str = "default"
    bass: str = "default"
    keys: str = "default"

    def __post_init__(self):
        for field in fields(self):
            if getattr(self, field.name) not in TEXTURE_CHOICES[field.name]:
                raise ValueError(f"unsupported arrangement {field.name}")
