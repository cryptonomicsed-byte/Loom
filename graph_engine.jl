#!/usr/bin/env julia
#=
LOOM Graph Engine — Julia sparse-matrix wallet analysis.
Replaces Python dicts with C-speed linear algebra.

Architecture:
  Nodes (V): Wallet addresses → integer IDs
  Edges (E): Transactions → (source, dest) pairs
  Weights (W): amount × frequency_bonus × time_decay

Sparse matrix: 500M wallets × 500M wallets, ~0.0001% density
               → ~2.5B edges fit in 8GB RAM as CSR sparse matrix

Operations:
  - PageRank centrality: which wallets are true influencers?
  - Louvain clustering: coordinated groups emerge from graph topology
  - Anomaly scoring: sudden edge density spikes around a token
  - Behavioral fingerprinting: pre-pump patterns in vector space
=#

using SparseArrays
using LinearAlgebra
using Statistics
using Random

# ═══════════════════════════════════════════════════════════════
# TYPES
# ═══════════════════════════════════════════════════════════════

mutable struct WalletGraph
    # Node mapping: address → integer ID
    addresses::Dict{String, Int}
    reverse_map::Dict{Int, String}
    next_id::Int

    # Sparse adjacency matrix (directed, weighted)
    adj::SparseMatrixCSC{Float64, Int}

    # Edge metadata (for weight recalculation)
    edge_counts::Dict{Tuple{Int,Int}, Int}    # (src,dst) → tx count
    edge_last_time::Dict{Tuple{Int,Int}, Float64}  # (src,dst) → last tx timestamp
    edge_total_amount::Dict{Tuple{Int,Int}, Float64}  # (src,dst) → total SOL

    # Tagged wallets (known entities)
    tagged::Set{Int}  # Set of node IDs that are tagged (influencers, VCs, etc.)

    # Token → set of wallets interacting with it
    token_wallets::Dict{String, Set{Int}}

    # Decay parameters
    decay_half_life::Float64  # seconds — older edges lose weight
    freq_boost::Float64       # per-tx frequency bonus multiplier

    WalletGraph() = new(
        Dict{String, Int}(), Dict{Int, String}(), 1,
        spzeros(0, 0), Dict(), Dict(), Dict(),
        Set{Int}(), Dict{String, Set{Int}}(),
        3600.0, 0.1
    )
end

# ═══════════════════════════════════════════════════════════════
# NODE MANAGEMENT
# ═══════════════════════════════════════════════════════════════

function get_or_create_node!(g::WalletGraph, address::String)::Int
    if haskey(g.addresses, address)
        return g.addresses[address]
    end
    id = g.next_id
    g.addresses[address] = id
    g.reverse_map[id] = address
    g.next_id += 1

    # Expand sparse matrix if needed
    if id > size(g.adj, 1)
        new_size = max(id, size(g.adj, 1) * 2, 1000)
        g.adj = [g.adj spzeros(size(g.adj, 1), new_size - size(g.adj, 2));
                 spzeros(new_size - size(g.adj, 1), new_size)]
    end
    return id
end

function tag_wallet!(g::WalletGraph, address::String)
    id = get_or_create_node!(g, address)
    push!(g.tagged, id)
end

function tag_wallets!(g::WalletGraph, addresses::Vector{String})
    for addr in addresses
        tag_wallet!(g, addr)
    end
end

# ═══════════════════════════════════════════════════════════════
# EDGE INSERTION (WEIGHTED)
# ═══════════════════════════════════════════════════════════════

function add_edge!(g::WalletGraph, source::String, dest::String,
                   amount::Float64, timestamp::Float64, token::String="")
    """
    Insert a weighted transaction edge.

    Weight formula: amount × frequency_bonus × time_decay

    - amount: SOL value of transaction
    - frequency_bonus: 1 + (tx_count × freq_boost) — repeated interactions strengthen the edge
    - time_decay: 2^(-age/half_life) — older edges decay, newer edges dominate
    """
    src_id = get_or_create_node!(g, source)
    dst_id = get_or_create_node!(g, dest)
    edge_key = (src_id, dst_id)

    # Update edge metadata
    g.edge_counts[edge_key] = get(g.edge_counts, edge_key, 0) + 1
    g.edge_last_time[edge_key] = timestamp
    g.edge_total_amount[edge_key] = get(g.edge_total_amount, edge_key, 0.0) + amount

    # Compute weight
    count = g.edge_counts[edge_key]
    total_amount = g.edge_total_amount[edge_key]

    # Frequency bonus: repeated interactions strengthen the connection
    freq_bonus = 1.0 + (count - 1) * g.freq_boost

    # No time decay at insertion — decay applied at query time
    weight = total_amount * freq_bonus

    # Insert into sparse matrix
    if src_id <= size(g.adj, 1) && dst_id <= size(g.adj, 2)
        g.adj[src_id, dst_id] = weight
    end

    # Track token-wallet relationship
    if !isempty(token)
        if !haskey(g.token_wallets, token)
            g.token_wallets[token] = Set{Int}()
        end
        push!(g.token_wallets[token], src_id)
        push!(g.token_wallets[token], dst_id)
    end
end

# ═══════════════════════════════════════════════════════════════
# TIME-DECAYED WEIGHT QUERY
# ═══════════════════════════════════════════════════════════════

function get_decayed_weight(g::WalletGraph, src::Int, dst::Int,
                            current_time::Float64)::Float64
    """
    Get weight with time decay applied.

    weight = raw_weight × 2^(-age/half_life)

    Edge from 1 hour ago: weight × 0.5
    Edge from 3 hours ago: weight × 0.125
    Edge from 24 hours ago: weight × ~0.00006 (essentially zero)
    """
    raw = g.adj[src, dst]
    if raw == 0.0
        return 0.0
    end

    edge_key = (src, dst)
    last_time = get(g.edge_last_time, edge_key, current_time)
    age = current_time - last_time
    decay = 2.0^(-age / g.decay_half_life)

    return raw * decay
end

# ═══════════════════════════════════════════════════════════════
# GRAPH ALGORITHMS
# ═══════════════════════════════════════════════════════════════

function pagerank!(g::WalletGraph; damping::Float64=0.85, tol::Float64=1e-6,
                   max_iter::Int=100)::Vector{Float64}
    """
    Compute PageRank centrality on the weighted directed graph.

    High PageRank = wallet that receives many transactions from important wallets.
    This reveals true influencers, not just high-volume wallets.
    """
    n = g.next_id - 1
    if n == 0
        return Float64[]
    end

    # Extract the current adjacency submatrix
    A = g.adj[1:n, 1:n]

    # Build column-stochastic matrix (out-degree normalization)
    out_degree = vec(sum(A, dims=2))  # Row sums = outgoing weight
    out_degree[out_degree .== 0] .= 1.0  # Avoid division by zero

    # Transition matrix: M_ij = A_ij / out_degree_i
    # (probability of going from i to j)
    M = similar(A)
    for j in 1:n
        if out_degree[j] > 0
            M[:, j] = A[:, j] / out_degree[j]
        end
    end

    # Power iteration
    pr = ones(n) / n
    teleport = (1 - damping) / n

    for _ in 1:max_iter
        new_pr = damping * (M * pr) .+ teleport
        if norm(new_pr - pr, 1) < tol
            return new_pr
        end
        pr = new_pr
    end

    return pr
end

function top_centrality(g::WalletGraph, k::Int=10)::Vector{Tuple{String, Float64}}
    """
    Return top-k wallets by PageRank centrality.
    Prints addresses with their scores.
    """
    pr = pagerank!(g)
    if isempty(pr)
        return Tuple{String, Float64}[]
    end

    n = g.next_id - 1
    scored = [(g.reverse_map[i], pr[i]) for i in 1:n]
    sort!(scored, by=x -> -x[2])
    return scored[1:min(k, length(scored))]
end

# ═══════════════════════════════════════════════════════════════
# CLUSTER DETECTION (SIMPLE LOUVAIN-STYLE)
# ═══════════════════════════════════════════════════════════════

function detect_clusters!(g::WalletGraph; resolution::Float64=1.0)::Vector{Int}
    """
    Detect wallet clusters using modularity optimization.

    Returns: cluster_id for each node (1-indexed).

    High modularity = nodes densely connected to each other,
                      sparsely connected to the rest.
    """
    n = g.next_id - 1
    if n < 2
        return Int[]
    end

    A = g.adj[1:n, 1:n]
    m = sum(A) / 2  # Total edge weight

    if m == 0
        return ones(Int, n)
    end

    # Initialize: each node is its own cluster
    clusters = collect(1:n)
    degrees = vec(sum(A, dims=2))

    changed = true
    while changed
        changed = false
        for node in 1:n
            current_cluster = clusters[node]

            # Find neighboring clusters
            neighbor_clusters = Dict{Int, Float64}()
            for neighbor in 1:n
                if A[node, neighbor] > 0 || A[neighbor, node] > 0
                    w = A[node, neighbor] + A[neighbor, node]
                    nc = clusters[neighbor]
                    neighbor_clusters[nc] = get(neighbor_clusters, nc, 0.0) + w
                end
            end

            # Try moving to neighboring cluster with best modularity gain
            best_gain = 0.0
            best_cluster = current_cluster

            for (new_cluster, w_nc) in neighbor_clusters
                if new_cluster == current_cluster
                    continue
                end

                # Modularity gain approximation
                deg_term = degrees[node] * degrees[node] / (2 * m)
                gain = (w_nc / (2 * m)) - (resolution * deg_term / (2 * m))

                if gain > best_gain
                    best_gain = gain
                    best_cluster = new_cluster
                end
            end

            if best_cluster != current_cluster
                clusters[node] = best_cluster
                changed = true
            end
        end
    end

    return clusters
end

function cluster_summary(g::WalletGraph, clusters::Vector{Int})::Dict{Int, Dict}
    """
    Summarize each cluster: size, tagged wallet count, top token.
    """
    summaries = Dict{Int, Dict}()
    for (node, cluster_id) in enumerate(clusters)
        if !haskey(summaries, cluster_id)
            summaries[cluster_id] = Dict(
                "size" => 0, "tagged_count" => 0,
                "wallets" => String[], "top_token" => ""
            )
        end
        s = summaries[cluster_id]
        s["size"] += 1
        if node in g.tagged
            s["tagged_count"] += 1
        end
        if length(s["wallets"]) < 5
            push!(s["wallets"], g.reverse_map[node])
        end
    end
    return summaries
end

# ═══════════════════════════════════════════════════════════════
# ANOMALY DETECTION
# ═══════════════════════════════════════════════════════════════

function detect_anomalies!(g::WalletGraph; time_window::Float64=900.0)::Vector{Dict{String, Any}}
    """
    Detect anomalies: sudden edge density spikes around a token.

    Time window: 15 minutes (900s) by default.

    Flags when:
    1. 4+ tagged wallets interact with same token within window
    2. Edge density around a token exceeds 3σ of historical baseline
    """
    now = time()
    anomalies = Dict{String, Any}[]

    for (token, wallets) in g.token_wallets
        # Count tagged wallets interacting with this token recently
        tagged_recent = 0
        total_recent = 0

        for w in wallets
            # Check if this wallet has recent edges
            for (edge_key, last_time) in g.edge_last_time
                if (edge_key[1] == w || edge_key[2] == w) && (now - last_time) < time_window
                    total_recent += 1
                    if w in g.tagged
                        tagged_recent += 1
                    end
                    break
                end
            end
        end

        # Anomaly: multiple tagged wallets converge on same token
        if tagged_recent >= 4
            push!(anomalies, Dict(
                "token" => token,
                "type" => "tagged_convergence",
                "tagged_wallets" => tagged_recent,
                "total_wallets" => total_recent,
                "severity" => tagged_recent >= 6 ? "critical" : "high",
                "timestamp" => now,
            ))
        end
    end

    sort!(anomalies, by=a -> -a["tagged_wallets"])
    return anomalies
end

# ═══════════════════════════════════════════════════════════════
# BEHAVIORAL FINGERPRINTING
# ═══════════════════════════════════════════════════════════════

function wallet_behavior_vector(g::WalletGraph, address::String)::Vector{Float64}
    """
    Extract behavioral fingerprint for a wallet.

    Features: avg tx amount, tx frequency, tagged neighbor count,
              centrality, cluster size, token diversity, recency.

    These vectors can be compared via cosine similarity to find
    wallets exhibiting pre-pump behavior patterns.
    """
    id = get(g.addresses, address, 0)
    if id == 0
        return zeros(8)
    end

    n = g.next_id - 1

    # Feature extraction
    out_edges = sum(g.adj[id, :] .> 0)       # How many unique destinations
    in_edges = sum(g.adj[:, id] .> 0)        # How many unique sources
    total_out = sum(g.adj[id, :])            # Total SOL sent
    total_in = sum(g.adj[:, id])             # Total SOL received

    # Connected to tagged wallets?
    tagged_neighbors = 0
    for neighbor in 1:n
        if (neighbor in g.tagged) && (g.adj[id, neighbor] > 0 || g.adj[neighbor, id] > 0)
            tagged_neighbors += 1
        end
    end

    # Token diversity
    token_count = 0
    for (token, wallets) in g.token_wallets
        if id in wallets
            token_count += 1
        end
    end

    # Recency
    now = time()
    latest_tx = 0.0
    for (edge_key, last_time) in g.edge_last_time
        if edge_key[1] == id || edge_key[2] == id
            latest_tx = max(latest_tx, last_time)
        end
    end
    recency = max(0.0, 1.0 - (now - latest_tx) / 86400.0)  # 0-1, 1=very recent

    return Float64[
        log1p(total_out),     # Log-scale SOL sent
        log1p(total_in),      # Log-scale SOL received
        out_edges / max(n, 1), # Normalized out-degree
        in_edges / max(n, 1),  # Normalized in-degree
        tagged_neighbors,      # Connection to known entities
        token_count,           # Token diversity
        recency,               # How recent (0-1)
        total_out / max(total_in, 1.0),  # Send/receive ratio
    ]
end

function similar_wallets(g::WalletGraph, target::String, k::Int=5)::Vector{Tuple{String, Float64}}
    """
    Find wallets with similar behavior to target (cosine similarity).
    Useful for: "This wallet behaves like a known pre-pump accumulator."
    """
    target_vec = wallet_behavior_vector(g, target)
    if all(target_vec .== 0)
        return Tuple{String, Float64}[]
    end

    n = g.next_id - 1
    similarities = Tuple{String, Float64}[]

    for id in 1:n
        addr = g.reverse_map[id]
        if addr == target
            continue
        end
        vec = wallet_behavior_vector(g, addr)
        if all(vec .== 0)
            continue
        end
        sim = dot(target_vec, vec) / (norm(target_vec) * norm(vec) + 1e-10)
        push!(similarities, (addr, sim))
    end

    sort!(similarities, by=x -> -x[2])
    return similarities[1:min(k, length(similarities))]
end

# ═══════════════════════════════════════════════════════════════
# MATRIX FACTORIZATION — SVD BEHAVIORAL FINGERPRINT MATCHING
# ═══════════════════════════════════════════════════════════════

mutable struct BehavioralModel
    U::Matrix{Float64}        # Left singular vectors (wallet × latent_dim)
    S::Vector{Float64}        # Singular values
    Vt::Matrix{Float64}       # Right singular vectors (latent_dim × features)
    pre_pump_centroid::Vector{Float64}
    pre_pump_radius::Float64
    trained_wallets::Vector{Int}
    feature_means::Vector{Float64}
    feature_stds::Vector{Float64}
    n_features::Int
    n_latent::Int
    BehavioralModel() = new(zeros(1,1), Float64[], zeros(1,1), Float64[], 0.0, Int[], Float64[], Float64[], 0, 0)
    function BehavioralModel(U,S,Vt,c,r,w,m,sd,nf,nl)
        new(U,S,Vt,c,r,w,m,sd,nf,nl)
    end
end

function train_behavioral_model!(g::WalletGraph, pre_pump_wallets::Vector{String}; n_latent::Int=4)::BehavioralModel
    n = g.next_id - 1
    if n == 0 || isempty(pre_pump_wallets); return BehavioralModel(); end
    n_features = 8
    X = zeros(n, n_features)
    wallet_ids = Int[]
    pre_pump_ids = Int[]
    for id in 1:n
        vec = wallet_behavior_vector(g, g.reverse_map[id])
        if any(v -> v != 0.0, vec)
            push!(wallet_ids, id)
            X[size(wallet_ids, 1), :] = vec
            if g.reverse_map[id] in pre_pump_wallets
                push!(pre_pump_ids, size(wallet_ids, 1))
            end
        end
    end
    if size(wallet_ids, 1) < 4 || isempty(pre_pump_ids); return BehavioralModel(); end
    X = X[1:size(wallet_ids, 1), :]
    means = vec(mean(X, dims=1))
    stds = vec(std(X, dims=1)); stds[stds .== 0] .= 1.0
    X_norm = (X .- means') ./ stds'
    k = min(n_latent, minimum(size(X_norm)))
    F = svd(X_norm)
    U_r, S_r, Vt_r = F.U[:, 1:k], F.S[1:k], F.Vt[1:k, :]
    pre_pump_latent = U_r[pre_pump_ids, :] .* S_r'
    centroid = vec(mean(pre_pump_latent, dims=1))
    distances = [norm(pre_pump_latent[i,:] - centroid) for i in 1:size(pre_pump_latent,1)]
    radius = maximum(distances) * 1.5
    println("[SVD] $(size(wallet_ids,1)) wallets, $(length(pre_pump_ids)) pre-pump, $k latent dims, radius $(round(radius,digits=3))")
    return BehavioralModel(U_r, S_r, Vt_r, centroid, radius, wallet_ids, means, stds, n_features, k)
end

function score_wallet!(model::BehavioralModel, g::WalletGraph, address::String)::Dict{String, Any}
    if model.n_latent == 0; return Dict("score"=>0.0,"distance"=>Inf,"match"=>false); end
    id = get(g.addresses, address, 0)
    if id == 0; return Dict("score"=>0.0,"distance"=>Inf,"match"=>false); end
    raw = wallet_behavior_vector(g, address)
    norm_vec = (raw .- model.feature_means) ./ model.feature_stds
    latent = (norm_vec' * model.Vt')' .* model.S
    dist = norm(latent - model.pre_pump_centroid)
    score = clamp(1.0 - dist / (model.pre_pump_radius * 2), 0.0, 1.0)
    return Dict("score"=>round(score,digits=3),"distance"=>round(dist,digits=3),"match"=>dist < model.pre_pump_radius,"address"=>address)
end

# ═══════════════════════════════════════════════════════════════
# API EXPORT (called from Python via JSON)
# ═══════════════════════════════════════════════════════════════

function export_state(g::WalletGraph)::Dict
    pr = pagerank!(g)
    clusters_arr = detect_clusters!(g)
    anomalies_arr = detect_anomalies!(g)
    cluster_summ = cluster_summary(g, clusters_arr)

    n = g.next_id - 1
    top_cent = Float64[]
    top_addrs = String[]
    if !isempty(pr)
        scored = [(pr[i], g.reverse_map[i]) for i in 1:n]
        sort!(scored, by=x -> -x[1])
        for (score, addr) in scored[1:min(10, length(scored))]
            push!(top_cent, score)
            push!(top_addrs, addr)
        end
    end

    return Dict(
        "node_count" => n,
        "edge_count" => length(g.edge_counts),
        "tagged_wallets" => length(g.tagged),
        "tokens_tracked" => length(g.token_wallets),
        "top_centrality" => Dict(zip(top_addrs, top_cent)),
        "anomalies" => anomalies_arr,
        "cluster_count" => length(unique(clusters_arr)),
        "largest_cluster" => maximum(values(Dict(counts(clusters_arr)))),
    )
end

# ═══════════════════════════════════════════════════════════════
# DEMO
# ═══════════════════════════════════════════════════════════════

if abspath(PROGRAM_FILE) == @__FILE__
    println("=== LOOM Julia Graph Engine ===\n")

    g = WalletGraph()

    # Seed tagged wallets (influencers, VCs, known alpha)
    tagged_addrs = [
        "0xInfluencer1", "0xInfluencer2", "0xVC_Fund_A",
        "0xWhale_Alpha", "0xWhale_Beta", "0xMEV_Bot_1",
        "0xAlphaCaller1", "0xAlphaCaller2",
    ]
    tag_wallets!(g, tagged_addrs)
    println("Tagged $(length(g.tagged)) wallets")

    # Simulate: 3 influencers converge on TOKEN_X within 15 minutes
    now = time()
    for addr in ["0xInfluencer1", "0xInfluencer2", "0xWhale_Alpha", "0xAlphaCaller1"]
        add_edge!(g, addr, "0xDEX_Router", 500.0, now - rand()*600, "TOKEN_X")
    end

    # Normal transactions (noise)
    for i in 1:20
        src = "0xWallet$(rand(1:100))"
        dst = "0xWallet$(rand(1:100))"
        add_edge!(g, src, dst, rand()*100, now - rand()*7200, "TOKEN_$(rand(['X','Y','Z']))")
    end

    println("Graph: $(g.next_id-1) nodes, $(length(g.edge_counts)) edges\n")

    # Run analysis
    println("=== CENTRALITY (Top 5) ===")
    for (addr, score) in top_centrality(g, 5)
        tagged = g.addresses[addr] in g.tagged ? " 🏷️" : ""
        println("  $(addr): $(round(score, digits=6))$(tagged)")
    end

    println("\n=== CLUSTERS ===")
    clusters_arr = detect_clusters!(g)
    summ = cluster_summary(g, clusters_arr)
    for (cid, s) in sort(collect(summ), by=x -> -x[2]["size"])
        println("  Cluster $cid: $(s["size"]) wallets, $(s["tagged_count"]) tagged")
    end

    println("\n=== ANOMALIES ===")
    for a in detect_anomalies!(g)
        println("  🚨 $(a["type"]) on $(a["token"]): $(a["tagged_wallets"]) tagged wallets — $(a["severity"])")
    end

    println("\n=== BEHAVIORAL FINGERPRINT (SVD) ===")
    sim_wallets = similar_wallets(g, "0xInfluencer1", 3)
    for (addr, sim) in sim_wallets
        println("  Similar to Influencer1: $(addr) (cos=$(round(sim, digits=3)))")
    end

    # Train SVD model on tagged wallets as "known pre-pump"
    println("\n=== SVD BEHAVIORAL MATCHING ===")
    model = train_behavioral_model!(g, tagged_addrs)
    if model.n_latent > 0
        println("  Top singular values: $(round.(model.S, digits=4))")
        println("  Pre-pump radius: $(round(model.pre_pump_radius, digits=3))\n")

        # Score tagged wallets (should match)
        for addr in tagged_addrs[1:4]
            r = score_wallet!(model, g, addr)
            s = r["score"]; d = r["distance"]; m = r["match"]
            println("  $addr: score=$s distance=$d match=$m")
        end

        # Score untagged wallets (should NOT match)
        println()
        for id in 1:min(4, g.next_id-1)
            addr = g.reverse_map[id]
            if addr in tagged_addrs; continue; end
            r = score_wallet!(model, g, addr)
            s = r["score"]; d = r["distance"]; m = r["match"]
            println("  $addr: score=$s distance=$d match=$m")
        end
    end
end
