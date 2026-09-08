# Sourced by the environment launcher before Xvnc becomes reachable.
# Checkpoint boots retain input isolation until their old lease is reconciled.
if [ -f /state/mutation-lease.json ]; then
  INPUT_FLAGS=(-AcceptPointerEvents=0 -AcceptKeyEvents=0 -AcceptCutText=0 -SendCutText=0 -AcceptSetDesktopSize=0)
fi
