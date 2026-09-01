from pathlib import Path
import importlib.util
import sqlite3


module_path = Path(__file__).resolve().parents[1] / "render_oncoseeing_v5_0_1.py"
spec = importlib.util.spec_from_file_location("render_report", module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_build_report_data_uses_csv_and_json(tmp_path):
    json_path = tmp_path / "input.json"
    csv_path = tmp_path / "hgvsg_AD_AF_AC.csv"
    json_path.write_text(
        '{"report_meta": {"brand_name": "Oncoseeing"}, '
        '"patient": {"name": "张三"}, '
        '"sample": {"sample_id": "S1"}, '
        '"test": {"cover_title_line1": "检测", "cover_title_line2": "报告", '
        '"report_subtitle": "sub", "english_title": "report"}, '
        '"result": {}}',
        encoding="utf-8",
    )
    csv_path.write_text(
        "HGVSG,AC,AD,AF\n"
        "10:g.1G>A,7,419,0.004951\n"
        "10:g.2G>T,11,2946,0.00385\n",
        encoding="utf-8",
    )

    data = module.build_report_data(json_path, csv_path)

    assert data["summary"]["variant_count"] == 2
    assert data["summary"]["high_af_count"] == 0
    assert data["variants"][0]["hgvsg"] == "10:g.1G>A"
    assert data["risk_cards"][0]["title"] == "总体风险"
    assert all("probability" not in item for item in data["result"]["cancer_risks"])


def test_cancer_risk_labels_do_not_sort_or_retain_numeric_probability():
    data = module.normalize_data({
        "result": {
            "cancer_risks": [
                {"cancer": "胃癌", "risk_label": "低"},
                {"cancer": "肺癌", "risk_label": "高"},
                {"cancer": "肝癌", "risk_label": "中"},
            ]
        }
    })

    assert data["result"]["cancer_risks"] == [
        {"cancer": "胃癌", "risk_label": "低"},
        {"cancer": "肺癌", "risk_label": "高"},
        {"cancer": "肝癌", "risk_label": "中"},
    ]


def test_default_conclusion_summarizes_matched_genes_sites_and_cancers():
    data = module.normalize_data({
        "cancer_findings": [{
            "cancer_type": "lung_carcinoma",
            "cancer_name": "肺癌",
            "site_count": 1,
            "risk_level": "中风险",
            "gene_statistics": [{"gene": "TP53"}],
            "variants": [{"hgvsg": "17:g.7579472C>T"}],
        }]
    })

    conclusion = data["result"]["conclusion"]
    assert "TP53 这 1 个基因" in conclusion
    assert "17:g.7579472C>T 共 1 种突变" in conclusion
    assert "肺癌的诱因" in conclusion


def test_build_cancer_findings_groups_hgvsgs_by_cancer(tmp_path):
    db_path = tmp_path / "mutations.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE mutation (cancer_type TEXT, hgvsg TEXT, gene_symbol TEXT, cosmic_sample_id TEXT)")
        conn.executemany(
            "INSERT INTO mutation VALUES (?, ?, ?, ?)",
            [
                ("lung_carcinoma", "1:g.1A>T", "TP53", "L1"),
                ("lung_carcinoma", "1:g.2C>G", "EGFR", "L1"),
                ("lung_carcinoma", "1:g.1A>T", "TP53", "L2"),
                ("breast_carcinoma", "1:g.1A>T", "TP53", "B1"),
            ],
        )

    findings = module.build_cancer_findings(
        db_path,
        [
            {"hgvsg": "1:g.1A>T", "ac": 4, "ad": 100, "af": 0.04},
            {"hgvsg": "1:g.2C>G", "ac": 2, "ad": 80, "af": 0.025},
        ],
    )

    assert [item["cancer_name"] for item in findings] == ["肺癌", "乳腺癌"]
    assert findings[0]["site_count"] == 2
    assert findings[0]["variants"][0]["hgvsg"] == "1:g.1A>T"
    assert findings[0]["risk_level"] == "高风险"
    assert findings[0]["gene_statistics"][0]["total_cases"] == 2


def test_coverage_and_risk_rows_include_every_configured_cancer(tmp_path):
    db_path = tmp_path / "mutations.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE mutation (cancer_type TEXT, hgvsg TEXT, gene_symbol TEXT, cosmic_sample_id TEXT)")
        conn.execute("INSERT INTO mutation VALUES ('lung_carcinoma', '1:g.1A>T', 'TP53', 'L1')")

    coverage = module.build_cancer_coverage_rows(db_path)
    risk_rows = module.build_risk_overview_rows([
        {"cancer_type": "lung_carcinoma", "site_count": 1, "risk_level": "中风险"}
    ])

    lung_coverage = next(row for row in coverage if row["cancer_type"] == "lung_carcinoma")
    lung_risk = next(row for row in risk_rows if row["cancer_name"] == "肺")
    assert len(coverage) == 20
    assert lung_coverage["gene_count"] == 1
    assert lung_coverage["site_count"] == 1
    assert lung_risk["assessment"] == "检出特异性突变点1个"
    assert lung_risk["risk_level"] == "中风险"
