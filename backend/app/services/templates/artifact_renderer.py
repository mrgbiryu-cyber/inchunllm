from __future__ import annotations

from typing import Dict, List, Any


def render_business_plan_html(plan: Dict[str, Any]) -> str:
    sections = plan.get("sections", [])
    section_html = "\n".join(
        f"<section><h2>{s['title']}</h2><p>{s['content']}</p></section>" for s in sections
    )
    return f"""
    <html>
      <body style='font-family: Arial, sans-serif; padding:24px; line-height:1.6'>
        <h1>{plan.get('title','AIBizPlan 사업계획서')}</h1>
        <p><b>Type:</b> {plan.get('company_type','')}</p>
        <p><b>Stage:</b> {plan.get('growth_stage','')}</p>
        {section_html}
      </body>
    </html>
    """.strip()


def render_matching_html(matching: Dict[str, Any]) -> str:
    rows = []
    for item in matching.get("items", []):
        gaps = ", ".join(item.get("gaps", []))
        rows.append(
            f"<tr><td>{item['category']}</td><td>{item['name']}</td><td>{item['score']}</td><td>{gaps}</td></tr>"
        )
    table = "".join(rows)
    return f"""
    <html>
      <body style='font-family: Arial, sans-serif; padding:24px;'>
        <h1>Certification/IP Matching</h1>
        <table border='1' cellpadding='8' cellspacing='0'>
          <thead><tr><th>Category</th><th>Name</th><th>Score</th><th>Gaps</th></tr></thead>
          <tbody>{table}</tbody>
        </table>
      </body>
    </html>
    """.strip()


def render_roadmap_html(roadmap: Dict[str, Any]) -> str:
    blocks: List[str] = []
    for year in roadmap.get("yearly_plan", []):
        actions = "".join(f"<li>{a}</li>" for a in year.get("actions", []))
        blocks.append(f"<h2>{year.get('year')}</h2><ul>{actions}</ul>")
    return f"""
    <html>
      <body style='font-family: Arial, sans-serif; padding:24px;'>
        <h1>{roadmap.get('title','Growth Roadmap')}</h1>
        {''.join(blocks)}
      </body>
    </html>
    """.strip()
