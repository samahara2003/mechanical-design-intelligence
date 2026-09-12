"""Small cross-language bridge for authoritative Engineering Core definitions."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace

from analysis_provenance import analysis_definition_fingerprint
from bracket_definition import bracket_analysis_definition
from engineering_domain import ModelVersionReference, analysis_definition_to_dict


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--gmsh-version", required=True)
    parser.add_argument("--calculix-version", required=True)
    args = parser.parse_args()
    definition = replace(
        bracket_analysis_definition(args.gmsh_version, args.calculix_version),
        model_version=ModelVersionReference(args.model_version),
    )
    print(json.dumps({
        "definition": analysis_definition_to_dict(definition),
        "fingerprint": analysis_definition_fingerprint(definition),
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
