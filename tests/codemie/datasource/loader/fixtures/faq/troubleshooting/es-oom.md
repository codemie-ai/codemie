---
title: "Elasticsearch keeps restarting (exit 137)"
---
The container is being OOM-killed. Check `docker compose ps -a` for exit code 137, then raise the
Docker/Colima memory allocation to at least 8 GB and restart.
