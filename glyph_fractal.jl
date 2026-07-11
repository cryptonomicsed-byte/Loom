#!/usr/bin/env julia
#=
LOOM Glyph Fractal Pass — REM-style compression planner for glyph memory.

Reads the glyph node export written by glyph_memory.py, measures the
box-counting fractal dimension of the memory-formation timeline
(Mandelbrot's burst-noise insight: information clusters in self-similar
bursts), clusters low-importance nodes by embedding similarity, and writes
back a plan of macro-glyph folds + prunes. glyph_memory.py applies the plan;
this file never mutates agent state — same contract as the Omo-Koda2 REM
planner (omokoda-memory/src/rem_fractal.jl).

Usage:  julia glyph_fractal.jl [nodes.json] [plan.json]
Files default to the LOOM daemon paths (/opt/loom/glyph_{nodes,plan}.json).
=#

using JSON

const NODES_FILE = length(ARGS) >= 1 ? ARGS[1] : "/opt/loom/glyph_nodes.json"
const PLAN_FILE  = length(ARGS) >= 2 ? ARGS[2] : "/opt/loom/glyph_plan.json"

"""Box-counting fractal dimension of a timestamp series, in [0, 1].
Steady memory formation → ~1.0; bursty → lower. <2 distinct points → 1.0."""
function fractal_dimension(timestamps::Vector{Float64})::Float64
    ts = sort(unique(timestamps))
    length(ts) < 2 && return 1.0
    span = ts[end] - ts[1]
    span <= 0 && return 1.0
    xs = Float64[]; ys = Float64[]
    for boxes in (2, 4, 8, 16, 32, 64)
        width = span / boxes
        occupied = length(unique(min(floor(Int, (t - ts[1]) / width), boxes - 1)
                                 for t in ts))
        push!(xs, log(1.0 / width))
        push!(ys, log(Float64(occupied)))
    end
    n = length(xs)
    denom = n * sum(xs .^ 2) - sum(xs)^2
    denom == 0 && return 1.0
    slope = (n * sum(xs .* ys) - sum(xs) * sum(ys)) / denom
    clamp(slope, 0.0, 1.0)
end

cosine(a::Vector{Float64}, b::Vector{Float64}) = sum(a .* b)

"""Greedy cosine clustering of noise nodes: each cluster grows from a seed,
absorbing nodes with similarity ≥ `threshold` to the seed. Deterministic
(nodes visited in sorted-id order), no training, fine for daemon scale."""
function cluster_noise(nodes::Vector{Any}; threshold::Float64=0.55)
    order = sort(nodes; by = n -> n["id"])
    clusters = Vector{Vector{Any}}()
    assigned = Set{String}()
    for seed in order
        seed["id"] in assigned && continue
        cluster = Any[seed]
        push!(assigned, seed["id"])
        seed_emb = Float64.(seed["embedding"])
        for candidate in order
            candidate["id"] in assigned && continue
            if cosine(seed_emb, Float64.(candidate["embedding"])) >= threshold
                push!(cluster, candidate)
                push!(assigned, candidate["id"])
            end
        end
        push!(clusters, cluster)
    end
    clusters
end

function plan(nodes::Vector{Any};
              noise_importance::Float64=0.35,
              min_fold_cluster::Int=3,
              similarity::Float64=0.55)
    fd = fractal_dimension(Float64[Float64(n["created_at"]) for n in nodes])
    noise = Any[n for n in nodes if Float64(n["importance"]) <= noise_importance]

    folds = Dict{String,Any}[]
    folded = Set{String}()
    for cluster in cluster_noise(noise; threshold=similarity)
        length(cluster) < min_fold_cluster && continue
        ids = sort(String[n["id"] for n in cluster])
        push!(folds, Dict("ids" => ids, "preview_count" => min(length(ids), 3)))
        union!(folded, ids)
    end

    prune_line = noise_importance / 2.0
    prune_ids = sort(String[n["id"] for n in noise
                            if Float64(n["importance"]) < prune_line &&
                               !(n["id"] in folded)])

    Dict("fractal_dimension" => fd,
         "folds" => folds,
         "prune_ids" => prune_ids,
         "nodes_analyzed" => length(nodes))
end

function main()
    payload = JSON.parsefile(NODES_FILE)
    nodes = Vector{Any}(payload["nodes"])
    result = plan(nodes)
    open(PLAN_FILE, "w") do io
        JSON.print(io, result)
    end
    println("[glyph-fractal] owner=$(get(payload, "owner", "?")) " *
            "nodes=$(length(nodes)) dim=$(round(result["fractal_dimension"]; digits=3)) " *
            "folds=$(length(result["folds"])) prunes=$(length(result["prune_ids"]))")
end

abspath(PROGRAM_FILE) == @__FILE__ && main()
