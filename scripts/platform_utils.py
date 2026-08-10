import os
import platform

def get_linux_distro():
    """
    Detects the Linux distribution from /etc/os-release.
    Returns a tuple (distro_id, pretty_name, is_arch_based).
    """
    if platform.system() != "Linux":
        return None, platform.system(), False

    os_release = {}
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if "=" in line:
                    key, value = line.rstrip().split("=", 1)
                    os_release[key] = value.strip('"')
    except Exception:
        return "unknown", "Unknown Linux", False

    distro_id = os_release.get("ID", "unknown").lower()
    pretty_name = os_release.get("PRETTY_NAME", distro_id.capitalize())
    
    # Check ID_LIKE for Arch or check if ID itself is an Arch-based distro
    id_like = os_release.get("ID_LIKE", "").lower().split()
    arch_based_ids = {"arch", "manjaro", "endeavouros", "garuda", "artix"}
    
    is_arch_based = distro_id in arch_based_ids or "arch" in id_like
    
    return distro_id, pretty_name, is_arch_based
