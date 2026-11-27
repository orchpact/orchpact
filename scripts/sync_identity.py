"""Copy the versioned Orchpact identity kit to public paths, or check for drift."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "design/identity"
PUBLIC = ROOT / "src/orchpact/static"
ASSETS = {
    "brand/identity-tokens.css": "tokens.css",
    "brand/orchpact-logo.svg": "brand/logo-light.svg",
    "brand/orchpact-logo-dark.svg": "brand/logo-dark.svg",
    "brand/favicon.ico": "brand/favicon.ico",
    "brand/apple-touch-icon.png": "brand/apple-touch-icon.png",
    "brand/LICENSE-wordmark.txt": "brand/LICENSE-wordmark.txt",
    "brand/orchpact-avatar.png": "brand/avatar-1024.png",
    "brand/orchpact-icon.svg": "brand/icon.svg",
    "brand/orchpact-logo.png": "brand/logo-light.png",
    "brand/orchpact-mark.svg": "brand/mark-mono.svg",
    "brand/orchpact-avatar.svg": "brand/avatar.svg",
    "favicon.svg": "brand/icon.svg",
    "account/favicon.svg": "brand/icon.svg",
    "account/orchpact-logo.svg": "brand/logo-light.svg"
}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report differences without writing")
    check = parser.parse_args().check
    stale = []
    for target, source in ASSETS.items():
        path = PUBLIC / target
        data = (KIT / source).read_bytes()
        if path.is_file() and path.read_bytes() == data:
            continue
        if check:
            stale.append(target)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    if stale:
        raise SystemExit("Out-of-date brand assets: " + ", ".join(stale))
    print(f"{len(ASSETS)} public brand assets match the design kit.")

if __name__ == "__main__":
    main()
