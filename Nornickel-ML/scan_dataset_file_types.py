from pathlib import Path
from collections import defaultdict
import argparse
import json
import csv


def human_size(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)

    for unit in units:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024

    return f"{size:.2f} PB"


def get_extension(path: Path) -> str:
    """
    Возвращает расширение файла в нижнем регистре.
    Для файлов без расширения возвращает '[no_extension]'.
    """
    suffix = path.suffix.lower().strip()

    if not suffix:
        return "[no_extension]"

    return suffix


def scan_dataset(dataset_path: Path, max_examples: int = 10) -> dict:
    stats = defaultdict(lambda: {
        "count": 0,
        "total_size_bytes": 0,
        "examples": []
    })

    total_files = 0
    total_size = 0
    errors = []

    for path in dataset_path.rglob("*"):
        try:
            if not path.is_file():
                continue

            ext = get_extension(path)
            size = path.stat().st_size

            stats[ext]["count"] += 1
            stats[ext]["total_size_bytes"] += size

            if len(stats[ext]["examples"]) < max_examples:
                stats[ext]["examples"].append(str(path.relative_to(dataset_path)))

            total_files += 1
            total_size += size

        except Exception as e:
            errors.append({
                "path": str(path),
                "error": str(e)
            })

    result = {
        "dataset_path": str(dataset_path.resolve()),
        "total_files": total_files,
        "total_size_bytes": total_size,
        "total_size_human": human_size(total_size),
        "extensions": {},
        "errors": errors
    }

    for ext, data in sorted(
        stats.items(),
        key=lambda item: item[1]["count"],
        reverse=True
    ):
        result["extensions"][ext] = {
            "count": data["count"],
            "total_size_bytes": data["total_size_bytes"],
            "total_size_human": human_size(data["total_size_bytes"]),
            "examples": data["examples"]
        }

    return result


def save_json(result: dict, output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def save_csv(result: dict, output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "extension",
            "count",
            "total_size_bytes",
            "total_size_human",
            "examples"
        ])

        for ext, data in result["extensions"].items():
            writer.writerow([
                ext,
                data["count"],
                data["total_size_bytes"],
                data["total_size_human"],
                " | ".join(data["examples"])
            ])


def print_summary(result: dict) -> None:
    print("\nDATASET SCAN RESULT")
    print("=" * 80)
    print(f"Dataset path: {result['dataset_path']}")
    print(f"Total files:  {result['total_files']}")
    print(f"Total size:   {result['total_size_human']}")
    print("=" * 80)

    print(f"{'Extension':<20} {'Count':<10} {'Size':<15}")
    print("-" * 80)

    for ext, data in result["extensions"].items():
        print(f"{ext:<20} {data['count']:<10} {data['total_size_human']:<15}")

    if result["errors"]:
        print("\nErrors:")
        for err in result["errors"][:20]:
            print(f"- {err['path']}: {err['error']}")


def main():
    parser = argparse.ArgumentParser(
        description="Recursively scan dataset folder and count file extensions."
    )

    parser.add_argument(
        "dataset_path",
        type=str,
        help="Path to dataset folder"
    )

    parser.add_argument(
        "--json",
        type=str,
        default="file_types_report.json",
        help="Path to output JSON report"
    )

    parser.add_argument(
        "--csv",
        type=str,
        default="file_types_report.csv",
        help="Path to output CSV report"
    )

    parser.add_argument(
        "--max-examples",
        type=int,
        default=10,
        help="Maximum example paths per extension"
    )

    args = parser.parse_args()

    dataset_path = Path(args.dataset_path)

    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset path does not exist: {dataset_path}")

    if not dataset_path.is_dir():
        raise NotADirectoryError(f"Dataset path is not a directory: {dataset_path}")

    result = scan_dataset(dataset_path, max_examples=args.max_examples)

    save_json(result, Path(args.json))
    save_csv(result, Path(args.csv))

    print_summary(result)

    print("\nSaved reports:")
    print(f"- JSON: {Path(args.json).resolve()}")
    print(f"- CSV:  {Path(args.csv).resolve()}")


if __name__ == "__main__":
    main()