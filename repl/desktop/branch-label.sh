#!/bin/sh
# Branch identity is supplied at container launch, including forks/restores.
printf 'Fork    ·    %s\n' "${FORK_BRANCH:-base}"
