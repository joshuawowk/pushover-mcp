# pushover-mcp

An [MCP](https://modelcontextprotocol.io) server for the [Pushover](https://pushover.net) notification API. Send push notifications to your phone and other devices from any MCP client. Single-file Python; runs over **stdio** or **streamable HTTP**.

## Tools
`send` (full parameter coverage incl. priority, sounds, and emergency receipts), `validate_user`, `list_sounds`, `get_limits`, `check_receipt`, `cancel_receipt`.

## Configuration (environment variables)
| Variable | Required | Description |
| --- | --- | --- |
| `PUSHOVER_TOKEN` | yes | Pushover application API token |
| `PUSHOVER_USER_KEY` | no | Default user/group key (tools accept a `user` override) |
| `PUSHOVER_DEVICE` | no | Default device name(s), comma-separated |
| `PUSHOVER_MCP_ALLOWED_HOSTS` | HTTP only | Host:port values the DNS-rebinding guard accepts |

## Run with Docker (stdio, e.g. Claude Desktop)
```json
{
  "mcpServers": {
    "pushover": {
      "command": "docker",
      "args": ["run","-i","--rm","-e","PUSHOVER_TOKEN","-e","PUSHOVER_USER_KEY","ghcr.io/joshuawowk/pushover-mcp:latest"],
      "env": { "PUSHOVER_TOKEN": "your-app-token", "PUSHOVER_USER_KEY": "your-user-key" }
    }
  }
}
```

## Self-host as a streamable-HTTP server
```bash
cp .env.example .env   # fill in your tokens
docker compose up -d   # serves MCP at http://<host>:8735/mcp
```

## Run from source
```bash
pip install "mcp>=1.2,<2" "httpx>=0.27"
python pushover_mcp.py                 # stdio
python pushover_mcp.py --http --host 0.0.0.0 --port 8735   # HTTP
```

## License
MIT © jwowk · https://jwowk.net
