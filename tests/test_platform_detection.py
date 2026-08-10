import unittest
from unittest.mock import patch, mock_open
import sys
import os

# Add scripts to sys.path
sys.path.append(os.path.abspath("scripts"))
from platform_utils import get_linux_distro

class TestPlatformDetection(unittest.TestCase):
    
    @patch("platform.system", return_value="Linux")
    @patch("builtins.open", new_callable=mock_open, read_data='ID=manjaro\nPRETTY_NAME="Manjaro Linux"\nID_LIKE=arch\n')
    def test_manjaro_detection(self, mock_file, mock_system):
        distro_id, pretty_name, is_arch_based = get_linux_distro()
        self.assertEqual(distro_id, "manjaro")
        self.assertEqual(pretty_name, "Manjaro Linux")
        self.assertTrue(is_arch_based)

    @patch("platform.system", return_value="Linux")
    @patch("builtins.open", new_callable=mock_open, read_data='ID=arch\nPRETTY_NAME="Arch Linux"\n')
    def test_arch_detection(self, mock_file, mock_system):
        distro_id, pretty_name, is_arch_based = get_linux_distro()
        self.assertEqual(distro_id, "arch")
        self.assertEqual(pretty_name, "Arch Linux")
        self.assertTrue(is_arch_based)

    @patch("platform.system", return_value="Linux")
    @patch("builtins.open", new_callable=mock_open, read_data='ID=ubuntu\nPRETTY_NAME="Ubuntu 24.04 LTS"\nID_LIKE="debian vendor"\n')
    def test_ubuntu_detection(self, mock_file, mock_system):
        distro_id, pretty_name, is_arch_based = get_linux_distro()
        self.assertEqual(distro_id, "ubuntu")
        self.assertEqual(pretty_name, "Ubuntu 24.04 LTS")
        self.assertFalse(is_arch_based)

    @patch("platform.system", return_value="Linux")
    @patch("builtins.open", new_callable=mock_open, read_data='ID=fedora\nPRETTY_NAME="Fedora Linux 40 (Workstation Edition)"\n')
    def test_fedora_detection(self, mock_file, mock_system):
        distro_id, pretty_name, is_arch_based = get_linux_distro()
        self.assertEqual(distro_id, "fedora")
        self.assertEqual(pretty_name, "Fedora Linux 40 (Workstation Edition)")
        self.assertFalse(is_arch_based)

    @patch("platform.system", return_value="Windows")
    def test_windows_detection(self, mock_system):
        distro_id, pretty_name, is_arch_based = get_linux_distro()
        self.assertIsNone(distro_id)
        self.assertEqual(pretty_name, "Windows")
        self.assertFalse(is_arch_based)

if __name__ == "__main__":
    unittest.main()
