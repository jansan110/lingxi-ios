#!/bin/sh
# Stage the Python business layer into the iOS app bundle.
#
# Mirrors build_tools/build_android.py: the same module whitelist, the same
# activity staging rule, and the same private-file check, so the iOS bundle
# carries exactly the files the Android APK carries.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUSINESS="$ROOT/LingxiWorkspace"
TARGET="$ROOT/app/LingxiWorkspace/Resources/business"

echo "==> staging business layer"
rm -rf "$TARGET"
mkdir -p "$TARGET"

python3 - "$BUSINESS" "$TARGET" <<'PY'
import shutil
import sys
from pathlib import Path

business, target = Path(sys.argv[1]), Path(sys.argv[2])

modules = (
    "app app_login android_bridge android_runtime http_transport "
    "bill_query usage_query services_query mobile_rights mobile_rights_auth "
    "mobile_rights_client mobile_rights_gateway mobile_rights_points mobile_rights_youth "
    "member_benefits member_benefits_protocol member_benefits_risk_runtime "
    "checkin_client checkin_flash checkin_scheduler checkin_teams "
    "coupon_client coupon_use coupon_device coupon_reminders coupon_notices "
    "coupon_captcha coupon_cashier_h5 coupon_alipay alipay_h5 "
    "lingxi_client lingxi_scheduler login_storage credential_cache network_access "
    "network_interfaces access_security monthly_coupon_auth monthly_coupon_client "
    "monthly_coupon_scheduler star_day_client star_day_auth star_day_scheduler "
    "star_day_sms star_day_crypto platform_manager feature_control feature_runtime "
    "navigation_status runtime_paths session_maintenance unified_logins update_checker "
    "net_age net_age_surprise_scheduler"
).split()

# iOS-only modules that must travel with the business layer.
ios_modules = ("ios_runtime", "cryptography_shim", "java")

for name in list(modules) + list(ios_modules):
    shutil.copy2(business / (name + ".py"), target / (name + ".py"))

for package in ("iosbridge",):
    shutil.copytree(business / package, target / package,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

for asset in ("app_login_public_key.json", "mobile_rights_gateway.json"):
    shutil.copy2(business / asset, target / asset)

for directory in ("static", "templates", "recharge/static", "recharge/templates"):
    shutil.copytree(business / directory, target / directory, dirs_exist_ok=True)

runtime = target / "recharge/member_benefits_runtime"
runtime.mkdir(parents=True, exist_ok=True)
for name in ("dfp_runtime.js", "wheel_runtime.js"):
    shutil.copy2(business / "recharge/member_benefits_runtime" / name, runtime / name)
shutil.copytree(business / "recharge/member_benefits_runtime/assets", runtime / "assets",
                dirs_exist_ok=True)

for name in ("__init__", "app", "accounts", "cmcc", "scheduler", "checkin", "device"):
    shutil.copy2(business / "recharge" / (name + ".py"), target / "recharge" / (name + ".py"))

sys.path.insert(0, str(business.parent / "src"))
try:
    from build_tools.activity_resources import stage_activities
except ImportError:
    stage_activities = None
if stage_activities is not None:
    stage_activities(business, target / "activities")
else:
    # Same rule as build_tools/activity_resources.py.
    activities = business / "activities"
    destination = target / "activities"
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("net_age_profile.json", "net_age_member_profile.json",
                 "net_age_surprise_profile.json"):
        shutil.copy2(activities / name, destination / name)
    for source in activities.iterdir():
        if source.is_file() and source.suffix == ".py":
            shutil.copy2(source, destination / source.name)
        elif source.is_dir() and (source.name == "checkin_common"
                                  or (source / "manifest.json").is_file()):
            shutil.copytree(source, destination / source.name,
                            ignore=shutil.ignore_patterns("__pycache__", "tests", "android"))

# Never ship runtime state.
for path in target.rglob("*"):
    if path.is_file() and (path.suffix in (".sqlite3", ".db", ".log", ".keystore", ".pyc")
                           or path.name.startswith(".")):
        raise SystemExit("private runtime data found in staging: " + str(path))

print("staged", sum(1 for p in target.rglob("*") if p.is_file()), "files")
PY

echo "==> done: $TARGET"
