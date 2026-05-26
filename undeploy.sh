#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=========================================="
echo " Disable NeMo Guardrails"
echo "=========================================="
echo ""

echo "=== Step 1: Restore LlamaStack to direct vLLM connection ==="
BACKUP="$SCRIPT_DIR/backup-llamastack-run-config.yaml"
if [ -f "$BACKUP" ]; then
  oc apply -f "$BACKUP"
  echo "Restored from backup"
else
  echo "No backup found. Manually restoring URL..."
  oc get configmap run-config -n llama-stack-rag -o yaml | \
  python3 -c "
import sys, yaml
cm = yaml.safe_load(sys.stdin)
config_str = cm['data']['config.yaml']
old_url = 'http://nemo-guardrails.nemo-guardrails.svc.cluster.local:80/v1'
new_url = 'http://gemma-3-27b-bf16-distributed-predictor.vszp.svc.cluster.local:8080/v1'
cm['data']['config.yaml'] = config_str.replace(old_url, new_url)
for key in ['resourceVersion', 'uid', 'creationTimestamp']:
    cm['metadata'].pop(key, None)
cm['metadata'].get('annotations', {}).pop('kubectl.kubernetes.io/last-applied-configuration', None)
yaml.dump(cm, sys.stdout, default_flow_style=False)
" | oc apply -f -
fi
echo ""

echo "=== Step 2: Restart LlamaStack ==="
oc rollout restart deployment/llamastack -n llama-stack-rag
oc rollout status deployment/llamastack -n llama-stack-rag --timeout=180s || true
echo ""

echo "=== Step 3: Delete NeMo Guardrails resources ==="
oc delete nemoguardrails nemo-guardrails -n nemo-guardrails --ignore-not-found
oc delete configmap nemo-config -n nemo-guardrails --ignore-not-found
oc delete secret api-token-secret -n nemo-guardrails --ignore-not-found
oc delete sa nemo-guardrails-sa -n nemo-guardrails --ignore-not-found
oc delete rolebinding nemo-guardrails-sa-view -n nemo-guardrails --ignore-not-found
oc delete namespace nemo-guardrails --ignore-not-found
echo ""

echo "=== Done ==="
echo "RAG pipeline is now connected directly to Gemma-3-27B without guardrails."
