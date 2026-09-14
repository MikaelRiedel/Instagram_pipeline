"""
Buffer API connection test.

What this does:
  1. Confirms your BUFFER_ACCESS_TOKEN works
  2. Finds your connected Instagram channel automatically
  3. Creates a DRAFT post (test image + caption) in your Buffer queue

Nothing is published to Instagram by this script. The post is saved as a
draft — you decide in the Buffer app whether and when to actually send it.

Setup (run these in your terminal, once):
    pip install requests
    export BUFFER_ACCESS_TOKEN="paste-your-token-here"

Then run:
    python3 test_buffer_connection.py
"""

import os
import sys
import requests

API_URL = "https://api.buffer.com"

# A stable, publicly hosted test image (Buffer requires image URLs to be
# public and stay reachable — no file uploads via this API).
TEST_IMAGE_URL = (
    "https://images.unsplash.com/photo-1742850541164-8eb59ecb3282"
    "?q=80&w=1200&auto=format&fit=crop"
)
TEST_CAPTION = "Test post from my automation pipeline — Buffer API connectivity check."


def graphql_request(token: str, query: str) -> dict:
    response = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"query": query},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if "errors" in payload:
        raise RuntimeError(f"Buffer API returned errors: {payload['errors']}")
    return payload["data"]


def get_organization_id(token: str) -> str:
    data = graphql_request(
        token,
        """
        query GetOrganizations {
          account {
            organizations {
              id
            }
          }
        }
        """,
    )
    orgs = data["account"]["organizations"]
    if not orgs:
        raise RuntimeError("No organizations found on this Buffer account.")
    if len(orgs) > 1:
        print(f"Note: found {len(orgs)} organizations, using the first one.")
    return orgs[0]["id"]


def get_instagram_channel_id(token: str, organization_id: str) -> str:
    data = graphql_request(
        token,
        f"""
        query GetChannels {{
          channels(input: {{ organizationId: "{organization_id}" }}) {{
            id
            displayName
            service
          }}
        }}
        """,
    )
    channels = data["channels"]
    print("Connected channels found:")
    for ch in channels:
        print(f"  - {ch['displayName']} ({ch['service']})  id={ch['id']}")

    instagram_channels = [c for c in channels if c["service"] == "instagram"]
    if not instagram_channels:
        raise RuntimeError(
            "No Instagram channel found. Double-check it's connected in Buffer."
        )
    return instagram_channels[0]["id"]


def create_draft_post(token: str, channel_id: str) -> dict:
    mutation = f"""
    mutation CreateDraftPost {{
      createPost(input: {{
        text: "{TEST_CAPTION}"
        channelId: "{channel_id}"
        schedulingType: automatic
        mode: addToQueue
        saveToDraft: true
        assets: [
          {{ image: {{ url: "{TEST_IMAGE_URL}" }} }}
        ]
        metadata: {{ instagram: {{ type: post, shouldShareToFeed: true }} }}
      }}) {{
        ... on PostActionSuccess {{
          post {{
            id
            text
          }}
        }}
        ... on MutationError {{
          message
        }}
      }}
    }}
    """
    data = graphql_request(token, mutation)
    return data["createPost"]


def main() -> None:
    token = os.environ.get("BUFFER_ACCESS_TOKEN")
    if not token:
        print("Set BUFFER_ACCESS_TOKEN first, e.g.:")
        print('  export BUFFER_ACCESS_TOKEN="your-token-here"')
        sys.exit(1)

    print("Looking up your organization...")
    org_id = get_organization_id(token)
    print(f"Found organization: {org_id}\n")

    print("Looking up your channels...")
    channel_id = get_instagram_channel_id(token, org_id)
    print(f"\nUsing Instagram channel: {channel_id}\n")

    print("Creating a DRAFT test post (will NOT publish automatically)...")
    result = create_draft_post(token, channel_id)

    if "message" in result:
        print(f"Buffer rejected the post: {result['message']}")
        sys.exit(1)

    print("\nSuccess! Draft post created:")
    print(f"  Post ID: {result['post']['id']}")
    print(f"  Text:    {result['post']['text']}")
    print("\nOpen the Buffer app to see it sitting in your drafts.")
    print("It will only go live on Instagram if you manually send it from there.")


if __name__ == "__main__":
    main()
