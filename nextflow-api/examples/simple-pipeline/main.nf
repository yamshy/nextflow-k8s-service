nextflow.enable.dsl=2

process HELLO {
    echo true

    input:
    val subject

    output:
    stdout

    script:
    """
    echo "Hello ${subject}!"
    """
}

workflow {
    HELLO(params.subject ?: 'World')
}
