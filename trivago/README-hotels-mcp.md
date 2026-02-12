# Fetch Hotels via Trivago MCP

Simple program to fetch hotel/accommodation results from the **Trivago MCP server** ([mcp.trivago.com](https://mcp.trivago.com)).

## What it does

- Connects to `https://mcp.trivago.com/mcp` using the MCP **Streamable HTTP** transport.
- Calls **trivago-search-suggestions** to resolve a location (e.g. city name) to Trivago’s `id`/`ns`.
- Calls **trivago-accommodation-search** with that location and your dates to get a list of accommodations.

## Requirements

- **Python 3.10+**
- MCP Python SDK: `pip install "mcp[cli]"`

Or install from the requirements file (from this folder):

```bash
pip install -r requirements-mcp-hotels.txt
```

## Usage

Run from the `trivago` folder:

```bash
cd trivago

# Default: Berlin, 2026-03-15 to 2026-03-18
python fetch_hotels_mcp.py

# Custom location and dates
python fetch_hotels_mcp.py "Paris" --arrival 2026-04-01 --departure 2026-04-05

# More options
python fetch_hotels_mcp.py "Tokyo" --arrival 2026-06-10 --departure 2026-06-17 --adults 2 --rooms 1 --max 5

# Output raw JSON
python fetch_hotels_mcp.py "Berlin" --json
```

Or from the project root:

```bash
python trivago/fetch_hotels_mcp.py "Berlin"
```

### Arguments

| Argument       | Default     | Description                    |
|----------------|-------------|--------------------------------|
| `query`        | Berlin      | Location (city/country name)   |
| `--arrival`    | 2026-03-15  | Arrival date (YYYY-MM-DD)     |
| `--departure`  | 2026-03-18  | Departure date (YYYY-MM-DD)   |
| `--adults`     | 2           | Number of adults              |
| `--rooms`      | 1           | Number of rooms               |
| `--max`        | 10          | Max results to show           |
| `--json`       | false       | Print raw JSON                |

## Trivago MCP server

The script uses the same Trivago MCP server you can add in Cursor/VS Code:

- **URL:** `https://mcp.trivago.com/mcp`
- **Docs:** [mcp.trivago.com/docs](https://mcp.trivago.com/docs)

No API key is required; the server is public.
