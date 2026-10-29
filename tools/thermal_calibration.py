#!/usr/bin/env python3
"""Calibrates THERMAL_TO_RGB (python_udfs/demo_udfs.py), the transform from thermal to RGB frame pixels, from the
silhouette of a person (and whatever warm things they hold) seen by both cameras.

    tools/thermal_calibration.py capture [--seconds 60] [--out thermal-calibration]
    tools/thermal_calibration.py fit [--out thermal-calibration]

`capture` stops the demo's queries (the camera can only be read by one query), starts a query that publishes the raw
thermal frames and the RGB frames (as JPEG) on MQTT, records them, and starts the demo's queries again. Stay out of
view for the first seconds (the empty scene is the background), then step in at the distance of a face from the
camera and move: arms up and to the sides, a hot cup held out, walk to the left and right edges of the frame. Keep
the cameras still. The cameras sit side by side, so the alignment holds for one distance; capture again at another
distance to see how much it changes.

`fit` takes the outline of the warm regions in every thermal frame (warmer than the empty scene: head, skin, a cup;
clothes may stay cold) and the edges in the RGB frame that are not part of the empty scene, and searches the scale,
rotation and shift that put the thermal outlines onto RGB edges, over all frames (chamfer matching: it needs an RGB
edge for every thermal edge, not the same silhouette, so a jacket that stays cold or a face that looks like the wall
do not matter). It prints the transform for THERMAL_TO_RGB with the distances before and after, and writes fit.png
(RGB frames, green the RGB edges, red the aligned thermal outline) to check it by eye.

Run it on the hub (the machine with the broker and the demo's compose project) with numpy and OpenCV, e.g. from a
venv: python3 -m venv /tmp/cv && /tmp/cv/bin/pip install numpy opencv-python-headless.
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOPIC = 'debug/thermal_calibration'
# In the repository, which the queries container mounts at /nebulastream
TOPOLOGY = '.thermal-calibration.yaml'
# The CLI's record of the started query, in the queries service's state volume, apart from the demo's queries
CLI_STATE = '/state/thermal-calibration'
MONO16 = 17825799
YUYV = 34603058


def run_demo(*args, **kwargs):
    return subprocess.run([os.path.join(REPO, 'run-demo.sh'), *args], cwd=REPO, **kwargs)


def nes_cli(*args, **kwargs):
    return run_demo('run', '--rm', '--no-deps', '-T', '-e', f'XDG_STATE_HOME={CLI_STATE}',
                    '--entrypoint', 'nes-cli', 'queries', '-t', TOPOLOGY, *args, **kwargs)


def write_capture_topology():
    """topology.yaml with its queries replaced by one that publishes every camera frame: the thermal frames raw, the
    RGB frames as JPEG (yuyv_to_jpg, which the demo already uses), both in base64."""
    topology = open(os.path.join(REPO, os.environ.get('NES_DEMO_TOPOLOGY', 'topology.yaml'))).read()
    head, rest = topology.split('\nquery:\n', 1)
    functions = rest.split('\nfunctions:\n', 1)[1]
    # The MQTT sink of the face query, for its broker and sink worker
    sink = re.search(r"INTO MQTT\('test/thermal-rgb-image'.*?\)\n", rest, re.DOTALL).group(0)
    sink = sink.replace('test/thermal-rgb-image', TOPIC).replace('1 AS "SINK"."QOS"', '0 AS "SINK"."QOS"')
    query = f'''query:
  - name: thermal_calibration
    query: |
      SELECT TIMESTAMP AS "timestamp", PIXEL_FORMAT AS "pixel_format", WIDTH AS "width", HEIGHT AS "height",
             CASE
                 WHEN PIXEL_FORMAT = {YUYV} THEN to_base64(yuyv_to_jpg(IMAGE, WIDTH, HEIGHT))
                 ELSE to_base64(IMAGE)
                 END AS "image"
      FROM video
      {sink}
functions:
'''
    with open(os.path.join(REPO, TOPOLOGY), 'w') as file:
        file.write(head + '\n' + query + functions)


def capture(args):
    os.makedirs(args.out, exist_ok=True)
    write_capture_topology()
    thermal, rgb = [], []
    subscriber = None
    try:
        print('stopping the demo queries (the camera is free then)')
        run_demo('stop', 'queries', check=True)
        subscriber = subprocess.Popen([os.path.join(REPO, 'run-demo.sh'), 'exec', '-T', 'mqtt', 'mosquitto_sub', '-t', TOPIC],
                                      cwd=REPO, stdout=subprocess.PIPE, bufsize=1 << 20)
        print('starting the capture query')
        nes_cli('start', check=True, stdout=subprocess.DEVNULL)
        print('waiting for the first frame')
        start = None
        stepped_in = False
        last_report = 0
        for line in subscriber.stdout:
            if not line.strip():
                continue
            record = json.loads(line)
            image = base64.b64decode(record['image'])
            now = time.monotonic()
            if start is None:
                start = now
                print(f'>>> STAY OUT OF VIEW: recording the empty scene for {args.empty_seconds:.0f} s')
            if not stepped_in and now - start >= args.empty_seconds:
                stepped_in = True
                print('>>> STEP IN: about 1 m from the camera, move arms, cup, walk to the edges of the frame')
            if record['pixel_format'] == MONO16:
                frame = np.frombuffer(image, np.uint16).reshape(record['height'], record['width'])
                thermal.append((record['timestamp'], now - start, frame))
            elif record['pixel_format'] == YUYV:
                rgb.append((record['timestamp'], image))
            if now - last_report >= 5:
                last_report = now
                print(f'  {args.seconds - (now - start):4.0f} s left, {len(thermal)} thermal and {len(rgb)} RGB frames')
            if now - start >= args.seconds:
                break
    finally:
        if subscriber is not None:
            subscriber.terminate()
        print('>>> DONE. Stopping the capture query and starting the demo queries again')
        nes_cli('stop', stdout=subprocess.DEVNULL)
        run_demo('up', '-d', 'queries')
        os.remove(os.path.join(REPO, TOPOLOGY))

    if not thermal or not rgb:
        sys.exit('no frames received')
    # Every thermal frame with the RGB frame closest in time
    rgb_times = np.array([t for t, _ in rgb])
    pairs_thermal, pairs_rgb, offsets, seconds = [], [], [], []
    for t, second, frame in thermal:
        i = int(np.argmin(np.abs(rgb_times - t)))
        offset_ms = (rgb_times[i] - t) / 1e6
        if abs(offset_ms) > args.max_offset_ms:
            continue
        pairs_thermal.append(frame)
        pairs_rgb.append(cv2.imdecode(np.frombuffer(rgb[i][1], np.uint8), cv2.IMREAD_COLOR))
        offsets.append(offset_ms)
        seconds.append(second)
    path = os.path.join(args.out, 'frames.npz')
    np.savez(path, thermal=np.stack(pairs_thermal), rgb=np.stack(pairs_rgb), offsets_ms=np.array(offsets),
             seconds=np.array(seconds), empty_seconds=args.empty_seconds)
    print(f'{len(pairs_thermal)} frame pairs (RGB within {args.max_offset_ms} ms, median '
          f'{np.median(np.abs(offsets)):.0f} ms) in {path}')


def clean(mask, open_size, close_size, min_area):
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, np.ones((open_size, open_size), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((close_size, close_size), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    keep = np.flatnonzero(stats[:, cv2.CC_STAT_AREA] >= min_area)
    return np.isin(labels, keep[keep > 0])


def rgb_edges(frame):
    return cv2.Canny(cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (0, 0), 1.5), 30, 90) > 0


def features(data, args):
    """Per frame with a person in view: the outline of the warm regions in the thermal frame (points) and the
    distance of every RGB pixel to the nearest edge that is not part of the empty scene (truncated)."""
    thermal = data['thermal'].astype(np.float32)
    empty = data['seconds'] < float(data['empty_seconds']) - 1  # a second of margin for stepping in
    if empty.sum() < 3:
        sys.exit(f'only {empty.sum()} frames of the empty scene')
    thermal_background = np.median(thermal[empty], axis=0)
    # Edges of the static scene stay where they are when the exposure changes; the dilation allows for a pixel of noise
    scene_edges = cv2.dilate(np.any([rgb_edges(data['rgb'][i]) for i in np.flatnonzero(empty)], axis=0).astype(np.uint8),
                             np.ones((5, 5), np.uint8)).astype(bool)
    print(f'{empty.sum()} frames of the empty scene as the background')
    height, width = thermal.shape[1:]
    outlines, distances, indices = [], [], []
    previous = None
    for i in np.flatnonzero(data['seconds'] > float(data['empty_seconds'])):
        warm = clean(cv2.GaussianBlur(thermal[i] - thermal_background, (0, 0), 2) > args.min_heat, 3, 7, args.min_area)
        # The RGB frame is up to --max-offset-ms off: only frames in which the person hardly moved since the last
        # thermal frame
        still = previous is not None and (warm & previous).sum() / max((warm | previous).sum(), 1) > args.min_stillness
        previous = warm
        if not 0.02 < warm.mean() < 0.7 or not still:
            continue
        contours, _ = cv2.findContours(warm.astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        points = np.concatenate([c.reshape(-1, 2) for c in contours])[::3].astype(np.float32)
        # The frame border is not an edge of the person
        points = points[(points[:, 0] > 3) & (points[:, 0] < width - 4) & (points[:, 1] > 3) & (points[:, 1] < height - 4)]
        edges = rgb_edges(data['rgb'][i]) & ~scene_edges
        if len(points) < 50 or edges.sum() < 200:
            continue
        distance = cv2.distanceTransform((~edges).astype(np.uint8), cv2.DIST_L2, 3)
        outlines.append(points)
        distances.append(np.minimum(distance, args.max_distance))
        indices.append(i)
    return outlines, distances, indices


def similarity(scale, angle, shift, center):
    """Scale and rotation (degrees) about the frame centre, then the shift."""
    matrix = cv2.getRotationMatrix2D(center, angle, scale)
    matrix[:, 2] += shift
    return matrix


class Chamfer:
    """Mean (truncated) distance of the mapped thermal outline points to the nearest RGB edge, over all frames."""

    def __init__(self, outlines, distances, max_distance):
        self.points = np.concatenate(outlines)
        self.frames = np.concatenate([np.full(len(p), k) for k, p in enumerate(outlines)])
        self.distances = np.stack(distances)
        self.max_distance = max_distance

    def mapped(self, matrix):
        return self.points @ matrix[:, :2].T + matrix[:, 2]

    def cost_of(self, mapped, values=False):
        height, width = self.distances.shape[1:]
        x = np.rint(mapped[:, 0]).astype(np.int64)
        y = np.rint(mapped[:, 1]).astype(np.int64)
        inside = (x >= 0) & (x < width) & (y >= 0) & (y < height)
        result = np.full(len(mapped), self.max_distance, np.float32)
        result[inside] = self.distances[self.frames[inside], y[inside], x[inside]]
        return result if values else float(result.mean())

    def cost(self, matrix):
        return self.cost_of(self.mapped(matrix))


def fit(args):
    data = np.load(os.path.join(args.out, 'frames.npz'))
    outlines, distances, indices = features(data, args)
    print(f'{len(indices)} frame pairs with a person in view, standing still')
    if len(indices) < 5:
        sys.exit('too few frames with a person in view, see the capture instructions (tools/thermal_calibration.py --help)')
    if len(indices) > args.max_frames:
        pick = np.linspace(0, len(indices) - 1, args.max_frames).round().astype(int)
        outlines, distances, indices = [outlines[i] for i in pick], [distances[i] for i in pick], [indices[i] for i in pick]
    chamfer = Chamfer(outlines, distances, args.max_distance)
    height, width = distances[0].shape
    center = (width / 2, height / 2)

    # Grid search with every 4th point: for every scale and rotation all shifts on a grid (shifting the mapped points
    # is cheap)
    sparse = Chamfer([o[::4] for o in outlines], distances, args.max_distance)
    shifts = np.arange(-args.max_shift, args.max_shift + 1, 8)
    best = None
    for scale in np.arange(0.5, 1.51, 0.025):
        for angle in np.arange(-3, 3.1, 1.5):
            mapped = sparse.mapped(similarity(scale, angle, (0, 0), center))
            for dx in shifts:
                for dy in shifts:
                    cost = sparse.cost_of(mapped + (dx, dy))
                    if best is None or cost < best[0]:
                        best = (cost, np.array([scale, angle, dx, dy], np.float64))
    best = (chamfer.cost(similarity(best[1][0], best[1][1], best[1][2:], center)), best[1])
    # Then coordinate descent with shrinking steps
    cost, params = best
    steps = np.array([0.0125, 0.5, 3.0, 3.0])
    while steps[0] > 0.0005:
        improved = False
        for k in range(4):
            for sign in (1, -1):
                candidate = params.copy()
                candidate[k] += sign * steps[k]
                value = chamfer.cost(similarity(candidate[0], candidate[1], candidate[2:], center))
                if value < cost:
                    cost, params, improved = value, candidate, True
        if not improved:
            steps /= 2
    scale, angle, shift = params[0], params[1], params[2:]
    matrix = similarity(scale, angle, shift, center)
    identity = np.eye(2, 3)
    near = lambda m: float((chamfer.cost_of(chamfer.mapped(m), values=True) <= 2).mean())
    print(f'\nscale {scale:.3f}, rotation {angle:.2f} degrees, shift ({shift[0]:.1f}, {shift[1]:.1f}) px about the centre')
    print(f'thermal outline to the nearest RGB edge: {chamfer.cost(identity):.1f} px unaligned, {cost:.1f} px aligned '
          f'(mean, capped at {args.max_distance:.0f}); within 2 px: {near(identity):.0%} unaligned, {near(matrix):.0%} aligned')
    print(f'THERMAL_TO_RGB = (({matrix[0, 0]:.5f}, {matrix[0, 1]:.5f}, {matrix[0, 2]:.2f}),\n'
          f'                  ({matrix[1, 0]:.5f}, {matrix[1, 1]:.5f}, {matrix[1, 2]:.2f}))')

    tiles = []
    for k in np.linspace(0, len(indices) - 1, min(4, len(indices))).round().astype(int):
        tile = data['rgb'][indices[k]].copy()
        tile[distances[k] == 0] = (0, 255, 0)
        for x, y in outlines[k] @ matrix[:, :2].T + matrix[:, 2]:
            cv2.circle(tile, (int(round(x)), int(round(y))), 1, (0, 0, 255), -1)
        tiles.append(tile)
    while len(tiles) % 2:
        tiles.append(np.zeros_like(tiles[0]))
    path = os.path.join(args.out, 'fit.png')
    cv2.imwrite(path, np.vstack([np.hstack(tiles[i:i + 2]) for i in range(0, len(tiles), 2)]))
    print(f'overlay: {path} (green: RGB edges of the person, red: thermal outline aligned)')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('step', choices=['capture', 'fit'])
    parser.add_argument('--out', default='thermal-calibration', help='directory for frames.npz and fit.png')
    parser.add_argument('--seconds', type=float, default=60, help='capture: recording time')
    parser.add_argument('--empty-seconds', type=float, default=8, help='capture: time of the empty scene at the start')
    parser.add_argument('--max-offset-ms', type=float, default=40, help='capture: largest time offset of a frame pair')
    parser.add_argument('--min-heat', type=float, default=120,
                        help='fit: raw units above the empty scene for the person (about 70 per degree Celsius)')
    parser.add_argument('--min-area', type=int, default=400, help='fit: smallest warm region in pixels that counts')
    parser.add_argument('--max-distance', type=float, default=15, help='fit: cap of the distance to an RGB edge in pixels')
    parser.add_argument('--min-stillness', type=float, default=0.9,
                        help='fit: overlap of the warm region with the previous thermal frame for a frame to count')
    parser.add_argument('--max-frames', type=int, default=40, help='fit: frame pairs used for the search')
    parser.add_argument('--max-shift', type=float, default=120, help='fit: largest shift in pixels searched')
    args = parser.parse_args()
    capture(args) if args.step == 'capture' else fit(args)


if __name__ == '__main__':
    main()
