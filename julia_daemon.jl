#!/usr/bin/env julia
#=
LOOM Julia Daemon — Continuous monitoring loop.
Runs on VPS. Exports state as JSON every 30s.
Webhooks fire when anomalies cross threshold.
=#

include("/opt/loom/graph_engine.jl")

using SparseArrays

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════

const STATE_FILE = "/opt/loom/julia_state.json"
const ALERT_FILE  = "/opt/loom/julia_alerts.json"
const INTERVAL    = 30   # seconds between cycles
const MAX_ALERTS  = 50

# Anomaly thresholds
const CONVERGENCE_THRESHOLD = 3   # tagged wallets converging on same token
const CENTRALITY_SPIKE      = 5.0  # unknown wallet centrality exceeding this

# ═══════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════

function main()
    println("[daemon] LOOM Julia Daemon starting...")

    g = WalletGraph()
    alerts = Dict{String, Any}[]
    last_state_export = 0.0

    # Seed tagged wallets
    tagged = String[
        "0xInfluencer1", "0xInfluencer2", "0xVC_Fund_A",
        "0xWhale_Alpha", "0xWhale_Beta", "0xMEV_Bot_1",
        "0xAlphaCaller1", "0xAlphaCaller2",
    ]
    tag_wallets!(g, tagged)
    println("[daemon] Seeded $(length(tagged)) tagged wallets")

    cycle = 0
    while true
        cycle += 1
        now = time()

        # ── Ingest would happen here (Helius webhooks → add_edge!) ──
        # For now, simulate transactions to keep the engine active
        if cycle % 5 == 0
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
                "uptime_seconds" => now - (now - cycle * INTERVAL),
            )

            write(STATE_FILE, """{"node_count":$(state["node_count"]),"edge_count":$(state["edge_count"]),"tagged_wallets":$(state["tagged_wallets"]),"tokens_tracked":$(state["tokens_tracked"]),"anomalies_active":$(state["anomalies_active"]),"alerts_new":$(state["alerts_new"]),"cycle":$(state["cycle"]),"timestamp":$(state["timestamp"]),"uptime_seconds":$(state["uptime_seconds"])}""")
            # Manual JSON for alerts array
            alert_strs = String[]
            for a in alerts[1:min(20, length(alerts))]
                push!(alert_strs, """{"type":"$(a["type"])","token":"$(get(a,"token",""))","tagged_wallets":$(a["tagged_wallets"]),"severity":"$(a["severity"])","timestamp":$(a["timestamp"]),"message":"$(replace(a["message"],"\""=>"'"))"}""")
            end
            write(ALERT_FILE, "[" * join(alert_strs, ",") * "]")
            last_state_export = now

            if cycle % 20 == 0
                println("[daemon] cycle=$cycle nodes=$(state["node_count"]) edges=$(state["edge_count"]) alerts=$(state["alerts_new"])")
            end
        end

        sleep(INTERVAL)
    end
end

main()
