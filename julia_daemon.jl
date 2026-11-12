#!/usr/bin/env julia
#=
LOOM Julia Daemon — Continuous monitoring loop.
Runs on VPS. Exports state as JSON every 30s.
Webhooks fire when anomalies cross threshold.
=#

include(joinpath(@__DIR__, "graph_engine.jl"))

using SparseArrays

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════
# Paths were hardcoded to /opt/loom (the retired Hostinger box). Now relative to
# this file, overridable via LOOM_JULIA_STATE_DIR. julia_api.py reads the same dir.

const STATE_DIR  = get(ENV, "LOOM_JULIA_STATE_DIR", joinpath(@__DIR__, "state"))
const STATE_FILE = joinpath(STATE_DIR, "julia_state.json")
const ALERT_FILE = joinpath(STATE_DIR, "julia_alerts.json")
const INTERVAL   = parse(Int, get(ENV, "LOOM_JULIA_INTERVAL", "30"))   # seconds between cycles
const MAX_ALERTS = 50

# SIMULATION IS OFF BY DEFAULT. With no real ingest wired (Helius webhooks →
# add_edge!), the original daemon invented transactions from placeholder wallets
# and exported the resulting "anomalies" as alerts — fabricated signals.
# LOOM_JULIA_SIMULATE=1 restores that for demos only, and every exported state and
# alert is then stamped source="simulated" so consumers can refuse it.
const SIMULATE = get(ENV, "LOOM_JULIA_SIMULATE", "0") == "1"
const SOURCE   = SIMULATE ? "simulated" : "live"

jesc(s) = replace(string(s), "\\" => "\\\\", "\"" => "\\\"", "\n" => " ")

# Atomic write: the API must never serve a half-written file.
function write_atomic(path, s)
    tmp = path * ".tmp"
    write(tmp, s)
    mv(tmp, path; force=true)
end

# Anomaly thresholds
const CONVERGENCE_THRESHOLD = 3   # tagged wallets converging on same token
const CENTRALITY_SPIKE      = parse(Float64, get(ENV, "LOOM_JULIA_CENTRALITY_SPIKE", "5.0"))  # unknown wallet centrality exceeding this

# ═══════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════

function main()
    println("[daemon] LOOM Julia Daemon starting... source=$SOURCE state_dir=$STATE_DIR interval=$(INTERVAL)s")
    mkpath(STATE_DIR)

    g = WalletGraph()
    alerts = Dict{String, Any}[]
    last_state_export = 0.0
    started = time()

    # Placeholder wallets exist only for simulation; a live daemon starts empty
    # until real ingest is wired.
    tagged = String[
        "0xInfluencer1", "0xInfluencer2", "0xVC_Fund_A",
        "0xWhale_Alpha", "0xWhale_Beta", "0xMEV_Bot_1",
        "0xAlphaCaller1", "0xAlphaCaller2",
    ]
    if SIMULATE
        tag_wallets!(g, tagged)
        println("[daemon] SIMULATION ON — seeded $(length(tagged)) placeholder wallets")
    else
        println("[daemon] live mode — no ingest wired yet; graph starts empty, no synthetic edges")
    end

    cycle = 0
    while true
        cycle += 1
        now = time()

        # ── Real ingest goes here (Helius webhooks → add_edge!) ──
        # Synthetic transactions only when explicitly simulating.
        if SIMULATE && cycle % 5 == 0
            src = tagged[rand(1:length(tagged))]
            dst = "0xWallet$(rand(1000:9999))"
            token = "TOKEN_$(rand(['X','Y','Z','W']))"
            amount = rand() * (rand() < 0.1 ? 5000.0 : 50.0)  # 10% chance of large tx
            add_edge!(g, src, dst, amount, now, token)
        end

        # ── Run analytics ──
        anomalies = detect_anomalies!(g)

        # Filter high-severity anomalies for alerts
        for a in anomalies
            if a["tagged_wallets"] >= CONVERGENCE_THRESHOLD
                # Check if this is a new alert (not duplicate)
                token = a["token"]
                existing = filter(x -> get(x, "token", "") == token, alerts)
                if isempty(existing)
                    pushfirst!(alerts, Dict(
                        "type" => "convergence",
                        "token" => token,
                        "tagged_wallets" => a["tagged_wallets"],
                        "severity" => a["severity"],
                        "timestamp" => now,
                        "message" => "$(a["tagged_wallets"]) high-tier wallets converging on $token — $(a["severity"])",
                    ))
                    msg = alerts[1]["message"]
                    println("[daemon] 🚨 ALERT: $msg")
                end
            end
        end

        # Detect centrality spikes (unknown wallet gains influence)
        if cycle % 10 == 0
            pr = pagerank!(g)
            n = g.next_id - 1
            for i in 1:n
                addr = g.reverse_map[i]
                if !(g.addresses[addr] in g.tagged) && pr[i] > CENTRALITY_SPIKE
                    existing = filter(x -> get(x, "address", "") == addr, alerts)
                    if isempty(existing)
                        pushfirst!(alerts, Dict(
                            "type" => "centrality_spike",
                            "address" => addr,
                            "score" => pr[i],
                            "timestamp" => now,
                            "message" => "Unknown wallet $addr has centrality $(round(pr[i],digits=2)) — possible originator",
                        ))
                    end
                end
            end
        end

        # Keep alert list bounded
        while length(alerts) > MAX_ALERTS; pop!(alerts); end

        # ── Export state ──
        if now - last_state_export > INTERVAL
            state = Dict(
                "node_count" => g.next_id - 1,
                "edge_count" => length(g.edge_counts),
                "tagged_wallets" => length(g.tagged),
                "tokens_tracked" => length(g.token_wallets),
                "anomalies_active" => length(anomalies),
                "alerts_new" => length(alerts),
                "cycle" => cycle,
                "timestamp" => now,
                "uptime_seconds" => now - started,
            )

            write_atomic(STATE_FILE, """{"node_count":$(state["node_count"]),"edge_count":$(state["edge_count"]),"tagged_wallets":$(state["tagged_wallets"]),"tokens_tracked":$(state["tokens_tracked"]),"anomalies_active":$(state["anomalies_active"]),"alerts_new":$(state["alerts_new"]),"cycle":$(state["cycle"]),"timestamp":$(state["timestamp"]),"uptime_seconds":$(state["uptime_seconds"]),"source":"$SOURCE"}""")
            # Manual JSON for alerts array. centrality_spike alerts carry address/score
            # and NO token/severity/tagged_wallets — the original indexed those keys
            # directly and crashed the daemon on the first spike. Use get() throughout.
            alert_strs = String[]
            for a in alerts[1:min(20, length(alerts))]
                push!(alert_strs, """{"type":"$(jesc(a["type"]))","token":"$(jesc(get(a,"token","")))","address":"$(jesc(get(a,"address","")))","score":$(get(a,"score",0.0)),"tagged_wallets":$(get(a,"tagged_wallets",0)),"severity":"$(jesc(get(a,"severity","")))","timestamp":$(a["timestamp"]),"message":"$(jesc(a["message"]))","source":"$SOURCE"}""")
            end
            write_atomic(ALERT_FILE, "[" * join(alert_strs, ",") * "]")
            last_state_export = now

            if cycle % 20 == 0
                println("[daemon] cycle=$cycle nodes=$(state["node_count"]) edges=$(state["edge_count"]) alerts=$(state["alerts_new"])")
            end
        end

        sleep(INTERVAL)
    end
end

main()
