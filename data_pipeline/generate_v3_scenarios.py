"""Generate the confirmed Version 3 scenario tables."""

from argparse import ArgumentParser
from pathlib import Path

from v3_model import WorkbookReference, generate_duckdb


DEFAULT_WORKBOOK = (
    Path(__file__).resolve().parent
    / "raw_excel"
    / "55007720_M14_Project_Grant_Projection_Model.xlsx"
)
DEFAULT_DATABASE = Path(__file__).resolve().parent / "processed" / "v3_scenarios.duckdb"


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--start-year", type=int, default=2023)
    parser.add_argument("--end-year", type=int, default=2070)
    parser.add_argument(
        "--sector",
        action="append",
        dest="sector_codes",
        help="Limit generation to one or more sector codes for validation.",
    )
    args = parser.parse_args()

    if not args.workbook.exists():
        raise FileNotFoundError(f"Workbook not found: {args.workbook}")
    args.database.parent.mkdir(parents=True, exist_ok=True)

    reference = WorkbookReference(args.workbook)
    scenario_count = generate_duckdb(
        reference=reference,
        database_path=args.database,
        years=range(args.start_year, args.end_year + 1),
        sector_codes=args.sector_codes,
    )
    print(f"Generated {scenario_count:,} scenarios in {args.database}")


if __name__ == "__main__":
    main()
