# Palworld dedicated server

A self-hosted Palworld dedicated server in Docker, on a machine at home. Solo play,
Steam only, reachable from the LAN and over Tailscale. Not exposed to the internet.

This repository holds the configuration. The server itself, the world and the backups
live on the machine — in `/d/palworld` throughout this document (an arbitrary path,
substitute your own).

What to put in place of the placeholders:

| Placeholder | Meaning |
|---|---|
| `<SERVER>` | ssh address of the machine running the server |
| `<SERVER_IP>` | its address on the local network |
| `<WORLD_ID>` | name of the world folder, 32 hex characters |
| `<STEAM_ID>` | your own SteamID64 |
| `<PLAYER_ID>` | the player id the server assigned you |

## Connecting

Palworld → Join Multiplayer Game → the address field at the bottom:

    <SERVER_IP>:8211

No password. Away from home, over Tailscale — the node's tailnet address, same port.

## Working with this repository

Run on your own machine, from the repository root:

    ./deploy.sh    # send compose.yaml to the server and apply it
    ./fetch.sh     # pull the current config off the server, see the drift

Both scripts read the server address and the stack path from `.env.local`, which is
untracked and written by hand:

    PALWORLD_HOST=palworld.local
    PALWORLD_DIR=/d/palworld

`.env` is deliberately not synced: it holds the admin password and exists only on the
server. The template is in `.env.example`.

## What lives where on the server

| Path | What |
|---|---|
| `/d/palworld/` | stack root, on the SATA SSD |
| `/d/palworld/compose.yaml` | the configuration (a copy of what is in this repository) |
| `/d/palworld/.env` | admin password, mode 600, owned by root |
| `/d/palworld/data/` | world, saves, server files, ~5 GB. Inside the container this is `/palworld` |
| `/d/palworld/data/backups/` | backups |

The container is `palworld-server`. Server version at the time of deployment —
`v1.0.3.101283`.

## Ports

| Port | Purpose | Exposure |
|---|---|---|
| 8211/udp | game | published on the LAN |
| 27015/udp | Steam query | published on the LAN |
| 25575/tcp | RCON | inside the container only |
| 8212/tcp | REST API | inside the container only |

No port forwarding on the router — the server is unreachable from the internet.

## Maintenance schedule

Times follow `TZ` inside the container, independent of the host's timezone.

| When | What |
|---|---|
| 03:30 | backup |
| 03:45 | update check |
| 04:00 | restart |
| 14:00 | restart, skipped if you are in game |

Backups are kept for 14 days; older ones are deleted automatically.

## Commands

Everything in this section runs **on the server**, that is, after:

    ssh <SERVER>

The admin password:

    grep ADMIN_PASSWORD /d/palworld/.env

Status and logs:

    cd /d/palworld
    docker compose ps
    docker compose logs -f --tail 50

Memory. The ceiling is 6442450944 (6 GB):

    docker exec palworld-server cat /sys/fs/cgroup/memory.current

Stop and start:

    cd /d/palworld
    docker compose stop
    docker compose start

The shutdown is graceful: the container is given 45 seconds to save the world, so wait
for it to finish and never kill the process by force. While the server is stopped, world
time stands still and base pals do no work.

A server stopped with `stop` will not come back up on its own — not on schedule, not
after the machine reboots. That is how `restart: unless-stopped` works: a manual stop is
taken as deliberate. Bring it back only with `start`.

Restart:

    cd /d/palworld && docker compose restart

Apply a compose.yaml you edited on the server directly:

    cd /d/palworld && docker compose up -d

Tear the container down entirely and bring it back:

    cd /d/palworld
    docker compose down
    docker compose up -d

The world is unaffected by this — it lives in `data/`, not inside the container.

RCON:

    docker exec -it palworld-server rcon-cli

Inside, `Info`, `ShowPlayers`, `Save`, `Broadcast <text>`, `Shutdown <sec> <text>`,
`DoExit`, `KickPlayer <SteamID>`, `BanPlayer <SteamID>`, `UnBanPlayer <SteamID>`,
`TeleportToPlayer <SteamID>` and `TeleportToMe <SteamID>` all work.

A manual backup, and the list of backups:

    docker exec palworld-server backup
    ls -lh /d/palworld/data/backups

Restore from a backup — it asks which:

    docker exec -it palworld-server restore

Update the server by hand:

    docker exec palworld-server update

## Migrating a local world onto the server

Verified in practice: a singleplayer world moved onto this server whole. The section is
kept as a working procedure — in case of a repeat, or a rollback.

The world travels in one piece: map, buildings, pals, bases. The only fiddly part is the
character — locally the host always sits in `00000000000000000000000000000001.sav`, and
a dedicated server does not understand that id, so it creates the player a new one.

### The tool

Since version 0.6 Palworld compresses saves with Oodle, and the marker at the head of
the file is `PlM` rather than the old `PlZ`. The tools most guides and videos point at
(`xNul/palworld-host-save-fix`, `cheahjs/palworld-save-tools`) have not been updated
since 2024 and fail with
`not a compressed Palworld save, found b'PlM' instead of b'PlZ'`.

The one that works is PST, https://github.com/deafdudecomputers/PalworldSaveTools
Releases carries a ready-made `win.exe`; nothing needs installing.

Its *Fix Host Save* **swaps two existing players**, it does not copy one over the other.
Which means the character on the server has to exist beforehand — hence the round trip
of upload → join → download → fix → upload.

### Paths

The local world on the gaming PC:

    %LOCALAPPDATA%\Pal\Saved\SaveGames\<STEAM_ID>\<world id>

The server's world folder, if you get to it over Samba with a share pointed at the
stack root:

    \\<SERVER_IP>\<share>\palworld\data\Pal\Saved\SaveGames\0\<WORLD_ID>

### Permissions: the main trap

Samba writes as its own user (root, in my case), the container runs as uid 1000. This
does not show up straight away: a file overwritten in place keeps its previous owner,
while one created anew arrives as `root:root`. So some of the files look fine, the server
starts and seems alive — but cannot save the world.

Which is why, after **every** write through the share:

    chown -R 1000:1000 /d/palworld/data/Pal/Saved/SaveGames
    find /d/palworld/data/Pal/Saved/SaveGames -type f -exec chmod 644 {} +
    find /d/palworld/data/Pal/Saved/SaveGames -type d -exec chmod 755 {} +

### The procedure

1. Stop the server and take a snapshot:

       cd /d/palworld
       docker compose stop
       tar czf pre-migration.tar.gz -C data/Pal/Saved/SaveGames/0 <WORLD_ID>

2. Copy onto the share, replacing, exactly two things: `Level.sav` and the whole
   `Players` folder. If you have opened Dimensional Pal Storage in game, `Players` will
   hold `*_dps.sav` files as well — stored pals live in those, do not pick through them
   one by one.

   **Nothing else.** Above all not `WorldOption.sav`: as long as it sits in the world
   folder it overrides `PalWorldSettings.ini`, meaning every setting from `compose.yaml`
   is silently ignored. Leave `LevelMeta.sav`, `LocalData.sav` and `GameUserSettings.ini`
   as the server's own, too.

3. Fix the permissions (see above) and start:

       cd /d/palworld && docker compose start

4. Join and create a **temporary** character. Run around for a minute so it autosaves,
   then quit. The server derives the player id from the Steam ID deterministically: one
   account always gets the same id, in any world. Read it off the filename under
   `Players`, or out of the server log.

5. Stop:

       cd /d/palworld && docker compose stop

6. Download `Level.sav` and the `Players` folder from the share into a separate folder
   on the PC, say `C:\palfix`. Work on local copies rather than over the network path:
   an SMB drop mid-write leaves a corrupted world behind.

7. PST → **Tools → Fix Host Save**, open `C:\palfix\Level.sav`.

   - Source Player — `00000000000000000000000000000001`, flagged as host, the larger file.
   - Target Player — `<PLAYER_ID>`, the temporary one, the smaller file.

   Do not get the order wrong: the tool exchanges the two, and the wrong way round leaves
   temp on the server and your real character in a dead slot. After the swap the file
   sizes trade places — that is the sign it worked.

8. Upload `Level.sav` and `Players` back, keeping only `<PLAYER_ID>.sav` inside
   `Players`. The `0001` file is of no use on the server — a dedicated server does not
   understand that id.

9. Fix the permissions, start, join. The character should be picked up on its own,
   with no creation screen.

### The map

Unlocked fast-travel points live with the client rather than in the world, in
`LocalData.sav`, and separately for every server. They can only be moved after the first
successful join: before that the destination folder simply does not exist.

1. Join the server, wait for the world to load, quit and **close the game completely** —
   otherwise Steam Cloud may overwrite the file you swapped in.
2. Under `%LOCALAPPDATA%\Pal\Saved\SaveGames\<STEAM_ID>\`, find the newest folder.
3. Rename the `LocalData.sav` in it to `LocalData.sav.bak`.
4. Put the `LocalData.sav` from the old local world in its place.

### Verifying

    ls -la /d/palworld/data/Pal/Saved/SaveGames/0/<WORLD_ID>/Players
    docker logs --since 10m palworld-server 2>&1 | grep -i joined

After a join and a quit the files should have grown, the timestamps should have changed,
and the owner should still be uid 1000 — which means the server really is writing saves.
The log should carry `joined the server ... Player id: <PLAYER_ID>` with no new character
created.

### Rolling back

    cd /d/palworld
    docker compose stop
    rm -rf data/Pal/Saved/SaveGames/0/<WORLD_ID>
    tar xzf pre-migration.tar.gz -C data/Pal/Saved/SaveGames/0
    chown -R 1000:1000 data/Pal/Saved/SaveGames
    docker compose start

The real safety net is the old world folder under `%LOCALAPPDATA%`. Do not touch it at
all: it is the one untouched copy from before any of this.

The world is bound to its folder through `DedicatedServerName` in
`data/Pal/Saved/Config/LinuxServer/GameUserSettings.ini`. As long as the folder name does
not change, that file needs no editing.

## What is configured, and why

- `COMMUNITY=false` — the server is not listed publicly.
- `CROSSPLAY_PLATFORMS=(Steam)` — Steam only.
- `SERVER_PASSWORD` empty — the server is only reachable from the LAN and my own tailnet
  as it is. Put the password back before opening 8211 to the outside.
- `ENABLE_INVADER_ENEMY=True` — raids are on by choice. They are the largest single
  source of memory growth, which is why there are two restarts a day rather than one.
- `AUTO_PAUSE_ENABLED=false` — **off on purpose.** Auto-pause freezes world time when
  nobody is online, and the whole point of a dedicated server is that base pals keep
  working. The price is that the leak accumulates around the clock.
- `mem_limit=6g` — a hard ceiling. It stops a bloated Palworld from OOM-killing the
  neighbouring services and sshd on a machine with 8 GB. This is the main thing
  protecting everything else running there.
- **CPU is not constrained at all** — no weight, no quota. Measured during play: 1.31 of
  four cores, the machine at 33%, no throttling, and the neighbours eating nothing at the
  time. The `cpu_shares=512` that used to sit here (weight 59 against the default 100
  everyone else has) was caution without measurement, and it worked the wrong way round:
  a media server survives falling a couple of seconds behind, whereas a late game tick is
  immediately visible to the player as a stutter. `mem_limit` is what protects the
  neighbours; memory was the real risk all along.
- `UPDATE_ON_BOOT` and auto-update — Palworld breaks client/server compatibility with
  every patch; without this you simply cannot get in after Steam updates.

## The hardware, and its limits

All of the above was written for one specific machine: a Dell OptiPlex 7040 Micro,
i5-6500T, 4 cores without hyper-threading, 3.1 GHz boost, **8 GB DDR4, 32 GB maximum**,
an NVMe for the system and a SATA SSD for `/d`. Ubuntu 22.04, Docker 29.4.

Other services live on the same machine: a reverse proxy, a media server, a torrent
client, file shares. That is where `mem_limit` comes from — it exists for their sake.

Memory will be the binding constraint long before single-threaded CPU is.

## When something breaks

**I cannot get in, and it worked yesterday.** Almost certainly Steam updated the client
and the server has not caught up. Check the logs, and run
`docker exec palworld-server update` if needed.

**The server restarted by itself.** It hit `mem_limit` and was OOM-killed. Check:

    dmesg -T | grep -i oom | tail

**Roll the config back.** The history is in git:

    git log --oneline -- compose.yaml
    git checkout <commit> -- compose.yaml && ./deploy.sh
