"""Create portable standard MIDI inputs; no InputEvent/Agent/recognition."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

import pretty_midi


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--soundfont", type=Path)
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    midi = pretty_midi.PrettyMIDI(initial_tempo=100.)
    lead = pretty_midi.Instrument(73, name="melody")
    for pitch, start, end in [(60, .24, .56), (64, .72, 1.04), (67, 1.2, 1.52), (64, 1.68, 2.)]:
        lead.notes.append(pretty_midi.Note(90, pitch, start, end))
    midi.instruments.append(lead)
    midi.write(str(args.out / "melody.mid"))
    (args.out / "prompt.txt").write_text("a warm acoustic jazz trio, piano, upright bass and gentle drums\n")
    if args.soundfont:
        shutil.copyfile(args.soundfont, args.out / "TimGM6mb.sf2")
    manifest = {"format": "Standard MIDI File + plain text; not a Magenta JSON protocol",
                "duration_s": 2.4,
                "files": {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
                          for f in args.out.iterdir() if f.is_file()}}
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")


if __name__ == "__main__":
    main()
