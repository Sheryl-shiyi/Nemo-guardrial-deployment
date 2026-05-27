#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=========================================="
echo " NeMo Guardrails Deployment"
echo "=========================================="
echo ""

echo "=== Step 1: Create namespace ==="
oc apply -f "$SCRIPT_DIR/00-namespace.yaml"
echo ""

echo "=== Step 2: Create ServiceAccount and RoleBinding ==="
oc apply -f "$SCRIPT_DIR/01-rbac.yaml"
echo ""

echo "=== Step 3: Create API token secret ==="
oc create secret generic api-token-secret \
  --from-literal=token=$(oc create token nemo-guardrails-sa --namespace=nemo-guardrails --duration=8760h) \
  --namespace=nemo-guardrails \
  --dry-run=client -o yaml | oc apply -f -
echo ""

echo "=== Step 4: Deploy NeMo Guardrails ConfigMap ==="
oc apply -f "$SCRIPT_DIR/02-nemo-config.yaml"
echo ""

echo "=== Step 5: Deploy NemoGuardrails CR ==="
oc apply -f "$SCRIPT_DIR/03-nemo-guardrails.yaml"
echo ""

echo "=== Step 6: Wait for NeMo pod to be ready ==="
oc rollout status deployment/nemo-guardrails -n nemo-guardrails --timeout=180s || true
echo ""

echo "=== Step 7: Increase route timeout for LLM inference ==="
oc annotate route nemo-guardrails -n nemo-guardrails haproxy.router.openshift.io/timeout=300s --overwrite
echo ""

echo "=========================================="
echo " RAG Pipeline Integration"
echo "=========================================="
echo ""

echo "=== Step 8: Update LlamaStack inference URL to NeMo Guardrails ==="
oc get configmap run-config -n llama-stack-rag -o yaml | \
python3 -c "
import sys, yaml
cm = yaml.safe_load(sys.stdin)
config_str = cm['data']['config.yaml']
old_url = 'http://gemma-3-27b-bf16-distributed-predictor.vszp.svc.cluster.local:8080/v1'
new_url = 'http://nemo-guardrails.nemo-guardrails.svc.cluster.local:80/v1'
if old_url not in config_str:
    print('INFO: URL already updated or not found, skipping', file=sys.stderr)
    sys.exit(0)
cm['data']['config.yaml'] = config_str.replace(old_url, new_url)
for key in ['resourceVersion', 'uid', 'creationTimestamp']:
    cm['metadata'].pop(key, None)
cm['metadata'].get('annotations', {}).pop('kubectl.kubernetes.io/last-applied-configuration', None)
yaml.dump(cm, sys.stdout, default_flow_style=False)
" | oc apply -f -
echo ""

echo "=== Step 9: Restart LlamaStack ==="
oc rollout restart deployment/llamastack -n llama-stack-rag
oc rollout status deployment/llamastack -n llama-stack-rag --timeout=180s || true
echo ""

echo "=== Step 10: Patch RAG UI (guardrails pre-check + Slovak greeting) ==="
oc create configmap rag-ui-overrides \
  --from-file=direct.py="$SCRIPT_DIR/rag-ui-patch/direct_patched.py" \
  --from-file=agent.py="$SCRIPT_DIR/rag-ui-patch/agent_original.py" \
  --from-file=chat.py="$SCRIPT_DIR/rag-ui-patch/chat_patched.py" \
  -n llama-stack-rag --dry-run=client -o yaml | oc apply -f -

# Ensure chat.py mount exists
oc get deployment rag -n llama-stack-rag -o jsonpath='{.spec.template.spec.containers[0].volumeMounts}' | grep -q chat.py || \
oc patch deployment rag -n llama-stack-rag --type=json -p='[
  {"op": "add", "path": "/spec/template/spec/containers/0/volumeMounts/-", "value": {"name": "rag-ui-overrides", "mountPath": "/app/llama_stack_ui/distribution/ui/page/playground/chat.py", "subPath": "chat.py"}}
]'
echo ""

echo "=== Step 11: Restart RAG UI ==="
oc rollout restart deployment/rag -n llama-stack-rag
oc rollout status deployment/rag -n llama-stack-rag --timeout=180s || true
echo ""

echo "=========================================="
echo " Deployment complete!"
echo "=========================================="
echo ""
echo "Test guardrails: bash test.sh"
echo "RAG UI: https://$(oc get route rag -n llama-stack-rag -o jsonpath='{.status.ingress[0].host}')"
