#!/usr/bin/env nextflow

/*
 * Portfolio Demo Pipeline
 * Demonstrates parallel data processing with scatter-gather pattern
 */

// Accept batch count parameter (1-12 range to fit within 50Gi/14 CPU quota)
params.batches = 5

process GENERATE {
    input:
    val batch_id

    output:
    path "batch_${batch_id}.csv"

    script:
    """
    echo "Generating batch ${batch_id}..."
    sleep 5
    for i in {1..100}; do
        echo "${batch_id},\$((RANDOM % 1000)),\$((RANDOM % 100))" >> batch_${batch_id}.csv
    done
    """
}

process ANALYZE {
    input:
    path datafile

    output:
    path "stats_${datafile}.json"

    script:
    """
    sleep 3
    lines=\$(wc -l < ${datafile})
    sum=\$(awk -F',' '{s+=\$2} END {print s}' ${datafile})
    avg=\$(awk -F',' '{s+=\$2; n++} END {print s/n}' ${datafile})
    echo '{"file":"${datafile}","lines":'\$lines',"sum":'\$sum',"average":'\$avg'}' > stats_${datafile}.json
    """
}

process REPORT {
    // Use workflow.runName to create unique output directories per run
    publishDir "/workspace/results/\${workflow.runName}", mode: 'copy'

    input:
    path stats

    output:
    path "report.json"

    script:
    """
    total_batches=\$(ls ${stats} | wc -l)
    echo '{"batches":[' > report.json
    paste -sd ',' ${stats} >> report.json
    echo '],"timestamp":"'\$(date -Iseconds)'","total_batches":'\$total_batches'}' >> report.json
    """
}

workflow {
    // Create channel from 1 to params.batches
    batches = Channel.of(1..params.batches)

    // Scatter: Generate data in parallel
    data = GENERATE(batches)

    // Process: Analyze each batch in parallel
    stats = ANALYZE(data)

    // Gather: Collect all results into single report
    REPORT(stats.collect())
}
