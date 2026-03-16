#!/usr/bin/env python3
"""
Fix Rotation Metadata

Finds all .lux.h5 files in the current directory, reads their rotation angle
metadata, determines a consensus value by majority vote, and overwrites any
mismatched files so all rotation angles are consistent.

Updates both the embedded HDF5 metadata and the JSON sidecar files.
"""

import glob
import json
import math
import os
import sys
from collections import Counter

import h5py


def read_h5_metadata(filepath):
    """Read and parse JSON metadata from an .h5 file's 'metadata' dataset."""
    with h5py.File(filepath, "r") as f:
        if "metadata" not in f:
            print(f"  WARNING: No 'metadata' dataset in {filepath}, skipping.")
            return None
        raw = f["metadata"][()]
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)


def write_h5_metadata(filepath, metadata_dict):
    """Write JSON metadata back into an .h5 file's 'metadata' dataset."""
    json_bytes = json.dumps(metadata_dict, indent=2).encode("utf-8")
    with h5py.File(filepath, "r+") as f:
        if "metadata" in f:
            del f["metadata"]
        f.create_dataset("metadata", data=json_bytes)


def read_json_sidecar(filepath):
    """Read a JSON sidecar file."""
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json_sidecar(filepath, data):
    """Write a JSON sidecar file."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def get_rotation_angles(proc_info):
    """Extract (start_deg, end_deg) from processingInformation acquisition metadata."""
    angles = []
    for acq in proc_info.get("acquisition", []):
        for sp in acq.get("stage_positions", []):
            if sp.get("type") == "rotation":
                angles.append((sp["start_deg"], sp["end_deg"]))
    return angles


def make_rotation_matrix_y(angle_deg):
    """Build a 3x3 Y-axis rotation matrix for the given angle in degrees.

    Standard Y-axis rotation:
        [[cos, 0, sin],
         [0,   1,   0],
         [-sin, 0, cos]]
    """
    theta = math.radians(angle_deg)
    c = round(math.cos(theta), 7)
    s = round(math.sin(theta), 7)
    return [
        [c, 0.0, s],
        [0.0, 1.0, 0.0],
        [-s, 0.0, c],
    ]


def mat_vec_mul(mat, vec):
    """Multiply a 3x3 matrix by a 3-vector."""
    return [
        sum(mat[i][j] * vec[j] for j in range(3))
        for i in range(3)
    ]


def vec_sub(a, b):
    """Subtract two 3-vectors: a - b."""
    return [a[i] - b[i] for i in range(3)]


def round_vec(v, decimals=7):
    """Round each element of a vector."""
    return [round(x, decimals) for x in v]


def is_rotation_matrix(matrix, angle_deg, tol=1e-4):
    """Check if a 3x3 matrix matches a Y-axis rotation by the given angle."""
    expected = make_rotation_matrix_y(angle_deg)
    for i in range(3):
        for j in range(3):
            if abs(matrix[i][j] - expected[i][j]) > tol:
                return False
    return True


def find_rotation_affine_index(affines, angle_deg):
    """Find the index of the rotation transform in affine_to_sample.

    Checks for 'type' field first, then falls back to matrix matching.
    """
    # First pass: look for type label
    for i, aff in enumerate(affines):
        if aff.get("type") in ("inter-stack:rotation", "rotation"):
            return i

    # Second pass: match matrix against cos/sin of the angle
    for i, aff in enumerate(affines):
        if is_rotation_matrix(aff["matrix"], angle_deg):
            return i

    return None


def compute_translation(rotation_matrix, offset_um):
    """Compute affine translation = R * offset - offset."""
    r_offset = mat_vec_mul(rotation_matrix, offset_um)
    return round_vec(vec_sub(r_offset, offset_um))


def update_processing_info(proc_info, new_start, new_end):
    """Update all rotation-related metadata in a processingInformation dict.

    Returns True if any changes were made.
    """
    changed = False

    for acq in proc_info.get("acquisition", []):
        # Find rotation stage position and its offset
        rotation_sp = None
        for sp in acq.get("stage_positions", []):
            if sp.get("type") == "rotation":
                rotation_sp = sp
                break

        if rotation_sp is None:
            continue

        old_start = rotation_sp["start_deg"]
        old_end = rotation_sp["end_deg"]

        if old_start == new_start and old_end == new_end:
            continue

        changed = True
        offset_um = rotation_sp.get("movement", {}).get("offset_um", [0.0, 0.0, 0.0])

        # Update stage_positions
        rotation_sp["start_deg"] = new_start
        rotation_sp["end_deg"] = new_end

        # Update affine_to_sample rotation transform
        affines = proc_info.get("affine_to_sample", [])
        rot_idx = find_rotation_affine_index(affines, old_start)
        if rot_idx is not None:
            new_matrix = make_rotation_matrix_y(new_start)
            new_translation = compute_translation(new_matrix, offset_um)
            affines[rot_idx]["matrix"] = new_matrix
            affines[rot_idx]["translation"] = new_translation

        # Update detection_directions
        det_dir = acq.get("detection", {}).get("direction", [0.0, 0.0, 1.0])
        new_matrix = make_rotation_matrix_y(new_start)
        new_det_dirs = [round_vec(mat_vec_mul(new_matrix, det_dir))]
        proc_info["detection_directions"] = new_det_dirs

    return changed


def update_json_sidecar_extra(sidecar_data, new_start, new_end):
    """Update metaData.stack.elements rotation entries in the JSON sidecar."""
    meta = sidecar_data.get("metaData", {})
    stack = meta.get("stack", {})
    for elem in stack.get("elements", []):
        if elem.get("name") == "r" or elem.get("target") == "r":
            elem["start"] = new_start
            elem["end"] = new_end


def main():
    h5_files = sorted(glob.glob("*.lux.h5"))

    if not h5_files:
        print("No .lux.h5 files found in the current directory.")
        sys.exit(0)

    print(f"Found {len(h5_files)} .lux.h5 file(s):\n")

    # Collect rotation angles from all files
    file_angles = {}  # filepath -> list of (start_deg, end_deg)
    all_angle_pairs = []

    for fp in h5_files:
        metadata = read_h5_metadata(fp)
        if metadata is None:
            continue

        proc_info = metadata.get("processingInformation", metadata)
        angles = get_rotation_angles(proc_info)

        if not angles:
            print(f"  {fp}: no rotation stage_positions found, skipping.")
            continue

        file_angles[fp] = angles
        all_angle_pairs.extend(angles)
        print(f"  {fp}: rotation angle(s) = {angles}")

    if not all_angle_pairs:
        print("\nNo rotation angles found in any file.")
        sys.exit(0)

    # Majority vote
    counts = Counter(all_angle_pairs)
    consensus_pair = counts.most_common(1)[0][0]
    consensus_start, consensus_end = consensus_pair

    print(f"\nConsensus rotation angle: start_deg={consensus_start}, end_deg={consensus_end}")
    print(f"  (found in {counts[consensus_pair]} of {len(all_angle_pairs)} rotation entries)\n")

    # Find files that need updating
    files_to_fix = []
    for fp, angles in file_angles.items():
        if any(a != consensus_pair for a in angles):
            files_to_fix.append(fp)

    if not files_to_fix:
        print("All rotation angles already match. Nothing to do.")
        sys.exit(0)

    print(f"Files to update: {len(files_to_fix)}\n")

    # Update each mismatched file
    for fp in files_to_fix:
        print(f"Updating {fp}...")

        # Update H5 metadata
        metadata = read_h5_metadata(fp)
        proc_info = metadata.get("processingInformation", metadata)
        if update_processing_info(proc_info, consensus_start, consensus_end):
            write_h5_metadata(fp, metadata)
            print(f"  H5 metadata updated.")

        # Update JSON sidecar
        json_base = os.path.splitext(fp)[0]  # removes .h5
        if json_base.endswith(".lux"):
            json_base = json_base[:-4]  # removes .lux
        json_path = json_base + ".json"

        if os.path.exists(json_path):
            sidecar = read_json_sidecar(json_path)

            # Update processingInformation in sidecar
            sidecar_proc = sidecar.get("processingInformation", None)
            if sidecar_proc:
                update_processing_info(sidecar_proc, consensus_start, consensus_end)

            # Update metaData.stack.elements
            update_json_sidecar_extra(sidecar, consensus_start, consensus_end)

            write_json_sidecar(json_path, sidecar)
            print(f"  JSON sidecar updated: {json_path}")
        else:
            print(f"  No JSON sidecar found at {json_path}")

    print(f"\nDone. Updated {len(files_to_fix)} file(s) to consensus rotation angle "
          f"(start_deg={consensus_start}, end_deg={consensus_end}).")


if __name__ == "__main__":
    main()
