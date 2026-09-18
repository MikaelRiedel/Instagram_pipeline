"""
One-off probe: what engagement metrics does Buffer actually hand back for
our published Instagram posts?

The whole stats loop depends on the answer. Buffer's docs describe metrics
generically (type/name/value/unit) without listing which ones arrive per
network, and Buffer's help pages say analytics is a paid feature -- so the
only way to know what a free plan returns is to ask it.

Run locally with BUFFER_ACCESS_TOKEN set, or via the check-metrics workflow
(which reads the token from repo secrets).
"""

from __future__ import annotations

import os
import sys

from pipeline_common import get_instagram_channel_id, get_organization_id, graphql_request

POSTS_WITH_METRICS = """
query GetPostsWithMetrics($organizationId: OrganizationId!, $channelId: ChannelId!) {
  posts(
    first: 20
    input: {
      organizationId: $organizationId
      filter: { status: [sent], channelIds: [$channelId] }
    }
  ) {
    edges {
      node {
        id
        dueAt
        text
        metrics { type name value unit }
        metricsUpdatedAt
      }
    }
  }
}
"""


def main() -> None:
    token = os.environ.get("BUFFER_ACCESS_TOKEN")
    if not token:
        print("Set BUFFER_ACCESS_TOKEN first.")
        sys.exit(1)

    org_id = get_organization_id(token)
    channel_id = get_instagram_channel_id(token, org_id)
    print(f"org={org_id} channel={channel_id}\n")

    data = graphql_request(
        token, POSTS_WITH_METRICS,
        {"organizationId": org_id, "channelId": channel_id},
    )
    edges = data["posts"]["edges"]
    print(f"sent posts returned: {len(edges)}\n")

    seen: set[str] = set()
    for edge in edges:
        node = edge["node"]
        metrics = node.get("metrics") or []
        print(f"- {node['id']}  due={node.get('dueAt')}  "
              f"metricsUpdatedAt={node.get('metricsUpdatedAt')}")
        print(f"  text: {(node.get('text') or '')[:70]!r}")
        if not metrics:
            print("  metrics: NONE returned")
        for m in metrics:
            seen.add(m.get("name", "?"))
            print(f"  metric: {m.get('name')} = {m.get('value')} "
                  f"({m.get('type')}, unit={m.get('unit')})")
        print()

    print("=" * 60)
    print("DISTINCT METRIC NAMES AVAILABLE:", sorted(seen) or "NONE")
    # The stats loop is built around saves; everything else is secondary.
    has_saves = any("save" in n.lower() for n in seen)
    print("SAVES AVAILABLE:", "YES" if has_saves else "NO -- stats loop must rank on something else")


if __name__ == "__main__":
    main()
