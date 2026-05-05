# BTCPay Server — E-Commerce Shop Integration (`shopkit.py`)

> **Branch:** `copilot/add-ecommerce-shop-script`  
> **License:** MIT  
> **Homepage:** [btcpayserver.org](https://btcpayserver.org)

A self-hosted, open-source Bitcoin payment processor with a full e-commerce deployment script (`shopkit.py`) that automates standing up an integrated storefront powered by BTCPay Server.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Installation](#installation)
   - [Step 1 — Clone the Repository](#step-1--clone-the-repository)
   - [Step 2 — Install System Dependencies](#step-2--install-system-dependencies)
   - [Step 3 — Install Python Dependencies](#step-3--install-python-dependencies)
   - [Step 4 — Install Docker & Docker Compose](#step-4--install-docker--docker-compose)
   - [Step 5 — Run the ShopKit Deployment Script](#step-5--run-the-shopkit-deployment-script)
5. [Configuration](#configuration)
   - [Environment Variables](#environment-variables)
   - [BTCPay Server Initial Setup](#btcpay-server-initial-setup)
   - [Store & Payment Configuration](#store--payment-configuration)
   - [Shopify Plugin (Optional)](#shopify-plugin-optional)
6. [Running the Application](#running-the-application)
7. [Building from Source (Advanced)](#building-from-source-advanced)
8. [Updating](#updating)
9. [Troubleshooting](#troubleshooting)
10. [Security Notes](#security-notes)
11. [Contributing](#contributing)

---

## Overview

This fork of [BTCPay Server](https://github.com/btcpayserver/btcpayserver) introduces **`shopkit.py`** — a comprehensive Python automation script that:

- **Provisions** a complete e-commerce environment (web server, database, BTCPay Server, NBXplorer) via Docker.
- **Configures** BTCPay Server stores, wallets, and payment methods automatically.
- **Integrates** with Shopify via the built-in Shopify plugin or with the BTCPay Point-of-Sale app for a self-hosted cart experience.
- **Manages** SSL certificates, DNS, and reverse-proxy (nginx/Caddy) setup.
- **Provides** operational helpers: log tailing, health checks, backup/restore, and service restarts.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   shopkit.py                        │
│         (Orchestration & Deployment Script)         │
└────────┬───────────────────────────────┬────────────┘
         │                               │
         ▼                               ▼
┌─────────────────┐             ┌─────────────────────┐
│   Docker Stack  │             │  Configuration Mgmt │
│  ─────────────  │             │  ─────────────────  │
│  BTCPayServer   │             │  .env file          │
│  NBXplorer      │             │  appsettings.json   │
│  PostgreSQL     │             │  nginx / Caddy conf │
│  Bitcoin Core   │             └─────────────────────┘
│  (optional LN)  │
└─────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────┐
│              BTCPay Server (ASP.NET Core)           │
│  ┌──────────────┐  ┌────────────┐  ┌─────────────┐ │
│  │  Point of    │  │  Shopify   │  │  Greenfield │ │
│  │  Sale Plugin │  │  Plugin    │  │  REST API   │ │
│  └──────────────┘  └────────────┘  └─────────────┘ │
└─────────────────────────────────────────────────────┘
```

---

## Prerequisites

| Requirement | Minimum Version | Notes |
|---|---|---|
| **OS** | Ubuntu 22.04 / Debian 12 | Other Linux distros supported; macOS for dev only |
| **Python** | 3.11+ | `python3 --version` |
| **Docker** | 24.0+ | [Install guide](https://docs.docker.com/engine/install/) |
| **Docker Compose** | v2.20+ | Included with Docker Desktop; `docker compose version` |
| **.NET SDK** | 10.0 | Only needed if building from source |
| **RAM** | 2 GB minimum | 4 GB recommended for mainnet with Lightning |
| **Disk** | 20 GB minimum | More for full Bitcoin node sync (~600 GB) |
| **Root / sudo access** | Required | `shopkit.py` calls `ensure_root()` at startup |

---

## Installation

### Step 1 — Clone the Repository

```bash
git clone https://github.com/Decipheredmedia/btcpayserver.git
cd btcpayserver
git checkout copilot/add-ecommerce-shop-script
```

### Step 2 — Install System Dependencies

On **Ubuntu / Debian**:

```bash
sudo apt-get update
sudo apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    git \
    curl \
    iproute2 \
    openssh-client \
    ca-certificates
```

On **macOS** (development only):

```bash
brew install python@3.11
```

### Step 3 — Install Python Dependencies

`shopkit.py` uses only the Python standard library, but if additional packages are needed:

```bash
# Create and activate an isolated virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# If a requirements.txt is present:
pip install -r requirements.txt

# Or install common helpers used by the script:
pip install requests pyyaml
```

### Step 4 — Install Docker & Docker Compose

```bash
# Add Docker's official GPG key and repository
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

echo "deb [arch=$(dpkg --print-architecture) \
  signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Allow your user to run Docker without sudo
sudo usermod -aG docker $USER
newgrp docker

# Verify
docker --version
docker compose version
```

### Step 5 — Run the ShopKit Deployment Script

`shopkit.py` is the single entrypoint for deployment. It **must be run as root** (or via `sudo`).

#### 5a. Quick-start (interactive mode)

```bash
sudo python3 shopkit.py
```

The script will interactively prompt for all required values (domain, email, Bitcoin network, etc.).

#### 5b. Non-interactive / scripted mode

Pass all required arguments directly:

```bash
sudo python3 shopkit.py \
  --domain        shop.yourdomain.com \
  --email         admin@yourdomain.com \
  --network       mainnet \
  --data-dir      /var/lib/btcpay \
  --enable-lightning
```

> **Tip:** Run `python3 shopkit.py --help` to see the full list of flags.

#### 5c. What the script does (step-by-step)

1. **Validates** that it is running as root (`ensure_root()`).
2. **Sets up logging** to stdout and to `/var/log/shopkit.log` (`setup_logging()`).
3. **Checks** that Docker and Docker Compose are installed and running.
4. **Creates** the data directory structure under `--data-dir` (default: `/var/lib/btcpay`).
5. **Generates** a `docker-compose.yml` tailored to your chosen network (mainnet / testnet / regtest) and optional services (Lightning, Tor).
6. **Writes** an `.env` file with all generated secrets (Postgres password, BTCPay API key seed, etc.).
7. **Pulls** Docker images and starts the stack with `docker compose up -d`.
8. **Waits** for BTCPay Server to become healthy (HTTP health-check loop).
9. **Configures** the initial admin account and store via the Greenfield REST API.
10. **Outputs** the access URL, admin credentials, and next steps.

---

## Configuration

### Environment Variables

After the first run, `shopkit.py` writes a `.env` file to `--data-dir`. You can also set these before running:

| Variable | Default | Description |
|---|---|---|
| `BTCPAY_HOST` | _(required)_ | Public domain name (e.g. `shop.example.com`) |
| `BTCPAY_DATADIR` | `/var/lib/btcpay` | Persistent data directory |
| `BTCPAY_NETWORK` | `mainnet` | `mainnet`, `testnet`, or `regtest` |
| `BTCPAY_POSTGRES` | _(auto-generated)_ | PostgreSQL connection string |
| `BTCPAY_ENABLE_SSH` | `false` | Expose SSH management interface |
| `LIGHTNING_IMPL` | `none` | `lnd`, `clightning`, `phoenixd`, or `none` |
| `NBITCOIN_NETWORK` | `mainnet` | Must match `BTCPAY_NETWORK` |
| `BTCPAY_ROOTPATH` | `/` | Sub-path if running behind a reverse proxy |
| `LETSENCRYPT_EMAIL` | _(required for SSL)_ | Email for Let's Encrypt certificate |

Edit the `.env` file and restart the stack to apply changes:

```bash
sudo python3 shopkit.py restart
# or
docker compose down && docker compose up -d
```

### BTCPay Server Initial Setup

After `shopkit.py` completes, open your browser at `https://<your-domain>`:

1. **Create the first admin account.**
   - Fill in username, email, and a strong password.
   - Click **Create Account**.

2. **Create a Store.**
   - Navigate to **Stores → Create a new store**.
   - Enter a store name (e.g. "My E-Commerce Shop").
   - Set your preferred currency and invoice expiry time.

3. **Connect a Bitcoin Wallet.**
   - Go to **Stores → (your store) → Wallets → Bitcoin → Setup**.
   - Choose one of:
     - **Watch-only (xpub)** — paste your hardware wallet's extended public key.
     - **Generate new wallet** — BTCPay creates and displays a seed phrase (back it up securely!).
     - **Import existing** — import from a file or Electrum wallet.

4. **Enable Payment Methods.**
   - In the store settings, navigate to **Payment Methods**.
   - Toggle **On-Chain Bitcoin** and/or **Lightning Network** (if Lightning is enabled).

### Store & Payment Configuration

#### Customising Invoice Settings

In your store settings:

- **Invoice Expiry:** Set how long a Bitcoin invoice stays valid (default: 15 minutes).
- **Speed Policy:** Choose `HighSpeed` (0-conf), `MediumSpeed` (1 conf), or `LowSpeed` (6 confs).
- **Currency Display:** Set the fiat currency customers see prices in.
- **Custom Checkout CSS:** Brand the payment page under **Checkout Appearance**.

#### Point-of-Sale App (Self-Hosted Shop)

1. Go to **Apps → Create new app → Point of Sale**.
2. Choose a view: **Product List**, **Product List with Cart**, or **Keypad**.
3. Add products with name, price, image URL, and description using the YAML template editor.
4. Click **Save** — your store is now live at the generated app URL.

Example YAML product template:

```yaml
- id: widget-blue
  title: Blue Widget
  price: 25.00
  priceType: Fixed
  image: https://example.com/images/widget-blue.png
  description: A premium blue widget

- id: widget-red
  title: Red Widget
  price: 30.00
  priceType: Fixed
  image: https://example.com/images/widget-red.png
```

### Shopify Plugin (Optional)

BTCPay Server ships with a built-in **Shopify Plugin** that lets you accept Bitcoin at checkout on your Shopify store.

#### Step-by-step Shopify setup

1. **In your Shopify Admin**, go to **Apps → App and sales channel settings → Develop apps → Create an app**.
   - App name: `BTCPayServer`
   - Set permissions: `write_orders`, `read_orders`, `write_payment_gateways`, `read_payment_gateways`.
   - Note your **API key** and **API secret (password)**.

2. **In BTCPay Server**, navigate to **Stores → (your store) → Plugins → Shopify**.

3. Fill in:
   - **Shop Name:** your Shopify store's `.myshopify.com` subdomain (without the suffix).
   - **API Key:** from step 1.
   - **API Password (Secret):** from step 1.

4. Click **Save and test connection** — BTCPay will verify the credentials.

5. **In Shopify Admin**, add a custom payment method:
   - Go to **Settings → Payments → Manual payment methods → Add manual payment method**.
   - Name: `Bitcoin via BTCPay Server`
   - Additional details: `Pay with Bitcoin. Your order will be confirmed after payment.`
   - Payment instructions: paste the JavaScript snippet shown in BTCPay's Shopify plugin settings.

6. Place a test order on your Shopify store to verify the full payment flow.

---

## Running the Application

### Start the stack

```bash
sudo python3 shopkit.py start
# or directly
docker compose up -d
```

### Stop the stack

```bash
sudo python3 shopkit.py stop
# or
docker compose down
```

### View logs

```bash
# All services
docker compose logs -f

# BTCPay Server only
docker compose logs -f btcpayserver

# Via shopkit helper
sudo python3 shopkit.py logs
```

### Check service health

```bash
docker compose ps
curl -f https://<your-domain>/health
```

### Run directly (without Docker, for development)

```bash
# Build first
./build.sh            # Linux/macOS
.\build.ps1           # Windows PowerShell

# Run
./run.sh              # Linux/macOS
.\run.ps1             # Windows PowerShell
```

---

## Building from Source (Advanced)

Requires **.NET SDK 10.0**:

```bash
# Install .NET SDK
wget https://dot.net/v1/dotnet-install.sh
chmod +x dotnet-install.sh
./dotnet-install.sh --channel 10.0

# Build the project
cd BTCPayServer
dotnet restore
dotnet publish --configuration Release --output /app/

# Run
dotnet /app/BTCPayServer.dll
```

Build and push a Docker image:

```bash
# Linux/macOS
./publish-docker.sh

# Windows
.\publish-docker.ps1
```

---

## Updating

```bash
# Pull latest code
git pull origin copilot/add-ecommerce-shop-script

# Pull updated Docker images and restart
sudo python3 shopkit.py update

# Or manually
docker compose pull
docker compose up -d
```

---

## Troubleshooting

### BTCPay Server is not reachable after install

1. Confirm containers are running: `docker compose ps`
2. Check BTCPay logs: `docker compose logs btcpayserver`
3. Verify your domain's DNS A record points to this server's IP.
4. Check that ports 80 and 443 are open in your firewall:
   ```bash
   sudo ufw allow 80/tcp
   sudo ufw allow 443/tcp
   sudo ufw reload
   ```

### SSL certificate errors

- Ensure `LETSENCRYPT_EMAIL` is set correctly in `.env`.
- Make sure your domain resolves publicly before running the script.
- Let's Encrypt has [rate limits](https://letsencrypt.org/docs/rate-limits/) — use staging for testing:
  ```bash
  sudo python3 shopkit.py --staging
  ```

### "Permission denied" when running shopkit.py

The script requires root privileges:

```bash
sudo python3 shopkit.py
```

### Docker daemon not running

```bash
sudo systemctl start docker
sudo systemctl enable docker
```

### Database connection errors

Check that the PostgreSQL container is healthy:

```bash
docker compose logs postgres
docker compose exec postgres psql -U btcpay -c "SELECT 1;"
```

### NBXplorer syncing slowly

NBXplorer needs to sync the Bitcoin blockchain. On mainnet, initial sync can take hours to days depending on hardware. Monitor progress:

```bash
docker compose logs nbxplorer | grep -i "height\|sync"
```

---

## Security Notes

- **Never expose PostgreSQL** ports externally. The database should only be accessible within the Docker network.
- **Back up your wallet seed phrase** immediately after wallet creation. BTCPay does not store the seed after initial display.
- **Use a strong admin password** and enable two-factor authentication under **Account → Manage → Two Factor Authentication**.
- **Keep Docker images updated** — run `shopkit.py update` regularly.
- **Review `SECURITY.md`** in this repository for responsible disclosure and known CVE information.
- **API keys** generated by shopkit.py are stored in `.env`. Restrict file permissions:
  ```bash
  chmod 600 /var/lib/btcpay/.env
  ```

---

## Contributing

Pull requests and issues are welcome!

1. Fork this repository.
2. Create a feature branch: `git checkout -b my-feature`.
3. Commit your changes following [Conventional Commits](https://www.conventionalcommits.org/).
4. Push and open a PR against this branch (`copilot/add-ecommerce-shop-script`).

Please read the upstream [BTCPay Server contribution guide](https://github.com/btcpayserver/btcpayserver/blob/master/CONTRIBUTING.md) for coding standards and test requirements.

---

## License

[MIT License](LICENSE) — free to use, modify, and distribute.

---

*Built on [BTCPay Server](https://btcpayserver.org) — Accept Bitcoin payments. Free, open-source & self-hosted.*
