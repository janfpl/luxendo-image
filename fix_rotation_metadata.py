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


def get_detection_directions(proc_info):
    """Extract detection_directions as a hashable tuple of tuples."""
    dd = proc_info.get("detection_directions", None)
    if dd is None:
        return None
    return tuple(tuple(v) for v in dd)


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



def extract_rotation_affine(proc_info):
    """Extract the rotation affine matrix and detection_directions from metadata.

    Returns (matrix, detection_directions) or (None, None) if not found.
    """
    for acq in proc_info.get("acquisition", []):
        for sp in acq.get("stage_positions", []):
            if sp.get("type") == "rotation":
                angle = sp["start_deg"]
                affines = proc_info.get("affine_to_sample", [])
                rot_idx = find_rotation_affine_index(affines, angle)
                matrix = affines[rot_idx]["matrix"] if rot_idx is not None else None
                det_dirs = proc_info.get("detection_directions", None)
                return matrix, det_dirs
    return None, None


def update_processing_info(proc_info, new_start, new_end, consensus_matrix,
                           consensus_detection_dirs):
    """Update all rotation-related metadata in a processingInformation dict.

    Copies the exact matrix and detection_directions from the consensus file
    instead of recalculating from trig, to avoid floating-point drift.
    Translation is left unchanged as it varies per xy tile.

    Returns True if any changes were made.
    """
    changed = False

    for acq in proc_info.get("acquisition", []):
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

        # Update stage_positions angles
        rotation_sp["start_deg"] = new_start
        rotation_sp["end_deg"] = new_end

        # Copy the exact rotation matrix from the consensus file
        # (translation is left as-is since it varies per xy tile)
        if consensus_matrix is not None:
            affines = proc_info.get("affine_to_sample", [])
            rot_idx = find_rotation_affine_index(affines, old_start)
            if rot_idx is not None:
                affines[rot_idx]["matrix"] = [row[:] for row in consensus_matrix]

        # Copy detection_directions from the consensus file
        if consensus_detection_dirs is not None:
            proc_info["detection_directions"] = [
                v[:] for v in consensus_detection_dirs
            ]

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
    # Default to ./snapshots if it exists, otherwise fall back to current directory
    snapshots_dir = os.path.join(os.getcwd(), "snapshots")
    if os.path.isdir(snapshots_dir):
        work_dir = snapshots_dir
        print(f"Found snapshots folder: {snapshots_dir}")
    else:
        work_dir = os.getcwd()
        print(f"No snapshots folder found, using current directory: {work_dir}")

    search_pattern = os.path.join(work_dir, "*.lux.h5")
    h5_files = sorted(glob.glob(search_pattern))

    if not h5_files:
        print(f"\nNo .lux.h5 files found in {work_dir}")
        sys.exit(0)

    print(f"Found {len(h5_files)} .lux.h5 file(s):\n")

    # Collect rotation angles and detection directions from all files
    file_angles = {}  # filepath -> list of (start_deg, end_deg)
    file_det_dirs = {}  # filepath -> tuple of tuples (hashable detection_directions)
    all_angle_pairs = []
    all_det_dirs = []

    for fp in h5_files:
        metadata = read_h5_metadata(fp)
        if metadata is None:
            continue

        proc_info = metadata.get("processingInformation", metadata)
        angles = get_rotation_angles(proc_info)
        det_dirs = get_detection_directions(proc_info)

        if not angles:
            print(f"  {fp}: no rotation stage_positions found, skipping.")
            continue

        file_angles[fp] = angles
        all_angle_pairs.extend(angles)

        if det_dirs is not None:
            file_det_dirs[fp] = det_dirs
            all_det_dirs.append(det_dirs)

        print(f"  {fp}: rotation angle(s) = {angles}")
        if det_dirs is not None:
            print(f"         detection_directions = {[list(v) for v in det_dirs]}")

    if not all_angle_pairs:
        print("\nNo rotation angles found in any file.")
        sys.exit(0)

    # Majority vote on rotation angles
    angle_counts = Counter(all_angle_pairs)
    consensus_pair = angle_counts.most_common(1)[0][0]
    consensus_start, consensus_end = consensus_pair

    print(f"\nConsensus rotation angle: start_deg={consensus_start}, end_deg={consensus_end}")
    print(f"  (found in {angle_counts[consensus_pair]} of {len(all_angle_pairs)} rotation entries)")

    # Majority vote on detection directions
    consensus_det_dirs_tuple = None
    if all_det_dirs:
        dd_counts = Counter(all_det_dirs)
        consensus_det_dirs_tuple = dd_counts.most_common(1)[0][0]
        print(f"\nConsensus detection_directions: {[list(v) for v in consensus_det_dirs_tuple]}")
        print(f"  (found in {dd_counts[consensus_det_dirs_tuple]} of {len(all_det_dirs)} files)")

    print()

    # Find files that need updating (angles OR detection_directions mismatch)
    files_to_fix = set()
    angle_mismatch = set()
    dd_mismatch = set()

    for fp, angles in file_angles.items():
        if any(a != consensus_pair for a in angles):
            files_to_fix.add(fp)
            angle_mismatch.add(fp)

    if consensus_det_dirs_tuple is not None:
        for fp in file_angles:
            file_dd = file_det_dirs.get(fp)
            if file_dd is not None and file_dd != consensus_det_dirs_tuple:
                files_to_fix.add(fp)
                dd_mismatch.add(fp)

    if not files_to_fix:
        print("All rotation angles and detection directions already match. Nothing to do.")
        sys.exit(0)

    if angle_mismatch:
        print(f"Files with angle mismatch: {len(angle_mismatch)}")
    if dd_mismatch:
        print(f"Files with detection_directions mismatch: {len(dd_mismatch)}")
    print(f"Total files to update: {len(files_to_fix)}\n")

    # Extract the exact rotation matrix from a consensus file so we can
    # copy it verbatim (no trig recalculation).
    consensus_matrix = None
    for fp, angles in file_angles.items():
        if all(a == consensus_pair for a in angles):
            ref_meta = read_h5_metadata(fp)
            ref_proc = ref_meta.get("processingInformation", ref_meta)
            consensus_matrix, _ = extract_rotation_affine(ref_proc)
            if consensus_matrix is not None:
                print(f"Using rotation matrix from reference file: {fp}")
                print(f"  matrix: {consensus_matrix}\n")
                break

    if consensus_matrix is None:
        print("WARNING: Could not extract rotation matrix from any consensus file.")
        print("         Angle values will be updated but matrices will be unchanged.\n")

    # Convert consensus detection_directions back to list-of-lists for writing
    consensus_det_dirs = None
    if consensus_det_dirs_tuple is not None:
        consensus_det_dirs = [list(v) for v in consensus_det_dirs_tuple]

    # Update each mismatched file
    for fp in sorted(files_to_fix):
        reasons = []
        if fp in angle_mismatch:
            reasons.append("angles")
        if fp in dd_mismatch:
            reasons.append("detection_directions")
        print(f"Updating {fp} ({', '.join(reasons)})...")

        # Update H5 metadata
        metadata = read_h5_metadata(fp)
        proc_info = metadata.get("processingInformation", metadata)
        h5_changed = update_processing_info(proc_info, consensus_start, consensus_end,
                                            consensus_matrix, consensus_det_dirs)

        # Also fix detection_directions even if angles already matched
        if fp in dd_mismatch and not (fp in angle_mismatch):
            if consensus_det_dirs is not None:
                proc_info["detection_directions"] = [v[:] for v in consensus_det_dirs]
                h5_changed = True

        if h5_changed:
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
                update_processing_info(sidecar_proc, consensus_start, consensus_end,
                                       consensus_matrix, consensus_det_dirs)
                # Fix detection_directions independently if only dd mismatched
                if fp in dd_mismatch and not (fp in angle_mismatch):
                    if consensus_det_dirs is not None:
                        sidecar_proc["detection_directions"] = [
                            v[:] for v in consensus_det_dirs
                        ]

            # Update metaData.stack.elements
            update_json_sidecar_extra(sidecar, consensus_start, consensus_end)

            write_json_sidecar(json_path, sidecar)
            print(f"  JSON sidecar updated: {json_path}")
        else:
            print(f"  No JSON sidecar found at {json_path}")

    print(f"\nDone. Updated {len(files_to_fix)} file(s).")
    print(f"  Consensus angle: start_deg={consensus_start}, end_deg={consensus_end}")
    if consensus_det_dirs is not None:
        print(f"  Consensus detection_directions: {consensus_det_dirs}")


if __name__ == "__main__":
    main()
