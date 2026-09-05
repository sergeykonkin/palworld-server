# Palworld on optiplex

The server runs on optiplex in the DMZ for four Steam players, with a join password
and direct connection by address. The world is created on its first start.

This repository and its private `.env` live on the management laptop. Docker Compose
uses the `optiplex` SSH context. Persistent data lives on optiplex under `/d`.

## Management connection

The laptop needs Docker CLI, Docker Compose and the SSH alias `optiplex`.
The SSH user needs access to the remote Docker socket without interactive sudo.

```sh
docker context create optiplex --docker host=ssh://optiplex
docker --context optiplex info
```

Create the context once. For an existing context, set its endpoint with
`docker context update optiplex --docker host=ssh://optiplex`.
All Docker commands below run from this repository on the laptop.
`--context optiplex` selects the remote engine for that command; the global default
context is independent of this project.

Docker management travels over SSH. The Docker API does not need a TCP listener.
See [Docker's SSH context documentation](https://docs.docker.com/engine/security/protect-access/).

## Storage and first start

| Location | Contents |
|---|---|
| Laptop: this repository | Compose configuration and documentation |
| Laptop: `.env` | Join and administrator passwords; excluded from git |
| optiplex: `/d/palworld` | Server installation, configuration and saves |
| optiplex: `/d/palworld/backups` | Container-managed backup archives |
| Container: `/palworld` | Bind mount of `/d/palworld` |

The bind source is an absolute path on **optiplex**, not on the laptop.
Compose creates it on first start (`create_host_path: true`). `/d` must be mounted
on the intended disk before starting the container.

One-time host preparation — confirm `/d` is a mountpoint:

```sh
ssh optiplex 'findmnt --mountpoint /d'
```

The image runs as root on boot, then `chown`s `/palworld` to its `steam` user
remapped to UID/GID `1000:1000`, so Docker creating the host dir as root is fine.
The host must mount `/d` before Docker starts containers, including after a reboot.

Create `.env` on the laptop from `.env.example`, set two different passwords,
and restrict its permissions:

```sh
cp -n .env.example .env
chmod 600 .env
```

`openssl rand -hex 16` generates a password. Only the join password is for players.
Compose rejects empty passwords. `.env` is read locally; its values become container
environment variables on optiplex. Users with Docker access can inspect them.

Validate, pull the image, then start:

```sh
docker --context optiplex compose config --quiet
docker --context optiplex compose pull
docker --context optiplex compose up -d --wait --wait-timeout 600
docker --context optiplex compose ps
```

The initial game download may take longer than the wait timeout. Check the logs if
the wait expires; a timeout does not stop the container.
Use `config --quiet` for validation: plain `config` prints resolved passwords.

## Player access and ports

`COMMUNITY=false` controls listing in the community browser. Internet reachability
depends on routing and firewall rules.

| Port | Purpose | Docker publication |
|---|---|---|
| 8211/udp | Game traffic | Host port 8211 |
| 25575/tcp | RCON | Container only |
| 8212/tcp | REST API | Container only |

For internet access, the router needs a forward for UDP 8211 to optiplex and the DMZ
firewall must allow the traffic. Router forwarding is configured separately.
The optiplex DMZ address is `10.4.5.6`; the forwarding destination is `10.4.5.6:8211/udp`.
RCON and REST API are managed through Docker over SSH and have no router forwards.
SSH access is for the management network or VPN.

Players connect in Palworld using `<SERVER_ADDRESS>:8211` and the join password.
From a network with access to the DMZ, the address is `10.4.5.6:8211`.

## Operations from the laptop

```sh
# Status, logs and resource consumption
docker --context optiplex compose ps
docker --context optiplex compose logs -f --tail 100
docker --context optiplex stats palworld-server

# Apply configuration, including values from .env
docker --context optiplex compose config --quiet
docker --context optiplex compose up -d --wait --wait-timeout 600

# Stop, start or restart the service
docker --context optiplex compose stop
docker --context optiplex compose start
docker --context optiplex compose restart

# Administration and backups
docker --context optiplex compose exec palworld autopause resume
docker --context optiplex compose exec palworld rcon-cli
docker --context optiplex compose exec palworld backup
docker --context optiplex compose exec palworld ls -lh /palworld/backups

# Copy backup archives to the laptop
docker --context optiplex compose cp palworld:/palworld/backups ./backups

# Update the game installation
docker --context optiplex compose exec palworld update
```

`restart` does not apply Compose or `.env` edits; use `up -d` for configuration.
The container has 45 seconds for graceful shutdown and saving.
With `restart: unless-stopped`, a manually stopped container requires an explicit
start, including after a host reboot.

The image uses the `latest` tag. `compose pull` downloads the container image;
`update` updates the game files. Applying a pulled image with `compose up -d`
can recreate the container, interrupting play.

## Configuration overrides

Compose specifies overrides of the container image defaults. Unspecified settings
inherit the image defaults, including PvE, raids, inventory-only death drops, ×1
multipliers, four bases per guild and 15 workers per base.

| Setting | Value |
|---|---|
| Timezone | Europe/Amsterdam, including daylight saving time |
| Server identity | optiplex; Friends server |
| Player access | Four Steam players; join and administrator passwords |
| RCON | Enabled; container only |
| Player list | Enabled |
| Empty-world pause | Enabled; after 30 minutes |
| Scheduled restart | Enabled; 04:00 |
| Game update check | Enabled; every hour at minute 45; five-minute player warning |
| Backup schedule | Every day at 03:30 |
| Backup retention | Delete archives older than 14 days |
| Container memory | 6 GiB limit, 2 GiB soft reservation |

The image defaults enable backups, REST API, player logging and updates on boot.
Scheduled restarts skip occupied servers by default. The game and auto-pause monitor
use UDP 8211 by default. Image updates can change inherited defaults.

Backups on `/d` share the same disk as the world. Keep a copy outside optiplex for
recovery from disk failure. Local archive copies belong in the ignored `backups/`
directory.

## Hardware

optiplex has 7.6 GiB of usable RAM and 7.9 GiB of swap. `/d` is an ext4 filesystem
on `/dev/sda1`. Docker Engine is installed on the host.
The 6 GiB container limit leaves memory for the host. Swap can absorb memory pressure
but is slower than RAM. Four-player performance needs verification during play.
The image documentation lists 16 GB RAM as its minimum:
[requirements](https://palworld-server-docker.loef.dev/).

## Validation scope

The image health check detects the game process. It does not verify player access,
save persistence, RCON or resume from an empty-world pause. These require checks
against the running server; public connectivity requires the router forward.

## Settings reference

Server and gameplay overrides are configured under `environment` in `compose.yaml`.
The image combines these with its defaults and generates `/palworld/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini`
from these variables at startup.

- [Container server settings](https://palworld-server-docker.loef.dev/getting-started/configuration/server-settings)
- [Container game settings](https://palworld-server-docker.loef.dev/getting-started/configuration/game-settings)
- [Engine settings](https://palworld-server-docker.loef.dev/getting-started/configuration/engine-settings)
- [Official Palworld parameters](https://docs.palworldgame.com/settings-and-operation/configuration/)

## Troubleshooting

For a failed start, read `compose logs` and check the remote bind path, permissions,
available disk space and memory. Inspect the container's exit state with:

```sh
docker --context optiplex inspect palworld-server --format '{{json .State}}'
```

`OOMKilled=true` indicates a container memory kill. Host kernel logs can provide
additional evidence. An unexpected restart alone does not establish its cause.

For a client/server version mismatch, check logs and update the game installation.
For connectivity failures, check the game listener and DMZ/router rules, then test
from outside the home network.
