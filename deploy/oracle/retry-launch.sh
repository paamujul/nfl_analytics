#!/usr/bin/env bash
# Keep retrying an Ampere A1 launch until Oracle has capacity.
#
# "Out of host capacity" is transient — free ARM capacity frees up constantly,
# but you have to be asking at the right moment. This rotates through every
# availability domain in your region and retries until one succeeds.
#
# Setup (once):
#   pip install oci-cli && oci setup config      # creates ~/.oci/config + API key
#   (paste the public key into OCI console -> Profile -> API keys)
#
# Then:
#   export COMPARTMENT_ID=ocid1.tenancy.oc1..xxx   # Profile -> Tenancy -> OCID
#   export SUBNET_ID=ocid1.subnet.oc1..xxx         # Networking -> VCN -> Subnet
#   export IMAGE_ID=ocid1.image.oc1..xxx           # see "find the image" below
#   bash retry-launch.sh
#
# Find the Ubuntu 24.04 ARM image OCID for your region:
#   oci compute image list --compartment-id "$COMPARTMENT_ID" \
#     --operating-system "Canonical Ubuntu" --operating-system-version "24.04" \
#     --shape VM.Standard.A1.Flex --query 'data[0].id' --raw-output
#
set -uo pipefail

: "${COMPARTMENT_ID:?set COMPARTMENT_ID}"
: "${SUBNET_ID:?set SUBNET_ID}"
: "${IMAGE_ID:?set IMAGE_ID}"

NAME="${NAME:-nfl-analytics}"
OCPUS="${OCPUS:-2}"
MEM_GB="${MEM_GB:-12}"
BOOT_GB="${BOOT_GB:-50}"
SSH_KEY_FILE="${SSH_KEY_FILE:-$HOME/.ssh/id_ed25519.pub}"
SLEEP="${SLEEP:-60}"

if [[ ! -f "$SSH_KEY_FILE" ]]; then
  echo "No SSH public key at $SSH_KEY_FILE (set SSH_KEY_FILE=...)" >&2
  exit 1
fi

echo "==> Listing availability domains"
# (portable read loop — macOS ships bash 3.2, which has no mapfile)
ADS=()
while IFS= read -r ad_line; do
  [[ -n "$ad_line" ]] && ADS+=("$ad_line")
done < <(oci iam availability-domain list \
  --compartment-id "$COMPARTMENT_ID" --query 'data[].name' --raw-output \
  | tr -d '[]", ' | grep -v '^$')

if [[ ${#ADS[@]} -eq 0 ]]; then
  echo "Could not list availability domains — check your OCI CLI config." >&2
  exit 1
fi
echo "    ${#ADS[@]} AD(s): ${ADS[*]}"
echo "==> Trying ${OCPUS} OCPU / ${MEM_GB} GB A1, retrying every ${SLEEP}s. Ctrl-C to stop."

attempt=0
while true; do
  for ad in "${ADS[@]}"; do
    attempt=$((attempt + 1))
    printf '[%s] attempt %d in %s ... ' "$(date +%H:%M:%S)" "$attempt" "$ad"
    # Note: no --fault-domain — pinning one only narrows available capacity.
    out=$(oci compute instance launch \
      --availability-domain "$ad" \
      --compartment-id "$COMPARTMENT_ID" \
      --subnet-id "$SUBNET_ID" \
      --image-id "$IMAGE_ID" \
      --shape VM.Standard.A1.Flex \
      --shape-config "{\"ocpus\":$OCPUS,\"memoryInGBs\":$MEM_GB}" \
      --boot-volume-size-in-gbs "$BOOT_GB" \
      --display-name "$NAME" \
      --assign-public-ip true \
      --ssh-authorized-keys-file "$SSH_KEY_FILE" \
      --wait-for-state RUNNING 2>&1)
    rc=$?

    if [[ $rc -eq 0 ]]; then
      echo "SUCCESS"
      echo "$out" | grep -o '"id": "ocid1.instance[^"]*"' | head -1
      echo
      echo "Instance is RUNNING. Get its public IP with:"
      echo "  oci compute instance list-vnics --instance-id <instance-ocid> --query 'data[0].\"public-ip\"' --raw-output"
      exit 0
    fi

    if grep -qi "Out of host capacity" <<<"$out"; then
      echo "no capacity"
    elif grep -qi "LimitExceeded\|quota" <<<"$out"; then
      echo "LIMIT/QUOTA hit — you may already have A1 instances using the free 4 OCPU/24 GB:"
      sed -n '1,5p' <<<"$out"
      exit 1
    else
      echo "error:"
      sed -n '1,8p' <<<"$out"
      exit 1
    fi
  done
  sleep "$SLEEP"
done
