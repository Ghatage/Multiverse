"""Port scheme. Branch index i in [0, 99]. Index 0 is reserved for the base/smoke container."""

BASE = 20000
STRIDE = 10
OFFSETS = {"vnc": 0, "cdp": 1, "repl": 2, "proxy": 3, "novnc": 4}
INTERNAL = {
    "vnc": 5901,
    "cdp": 9222,
    "repl": 7000,
    "novnc": 6080,
}  # inside the container


def port_block(index: int) -> dict[str, int]:
    assert 0 <= index < 100, "port scheme supports 100 branches"
    return {name: BASE + STRIDE * index + off for name, off in OFFSETS.items()}


def docker_publish_args(index: int) -> list[str]:
    p = port_block(index)
    return [f"-p127.0.0.1:{p[k]}:{INTERNAL[k]}" for k in INTERNAL]


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("index", type=int, nargs="?", default=0)
    parser.add_argument("--docker", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    print(
        " ".join(docker_publish_args(args.index))
        if args.docker
        else json.dumps(port_block(args.index))
    )
