package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestPrivateInstallAndInterruptedStateRejectUpstreamUpgrade(t *testing.T) {
	for _, name := range []string{"private-native.json", "private-native-owner.json", "private-native-pending.json"} {
		t.Run(name, func(t *testing.T) {
			root := t.TempDir()
			if err := os.WriteFile(filepath.Join(root, name), []byte(`{"synthetic":true}`), 0o600); err != nil {
				t.Fatal(err)
			}
			if err := rejectPrivateUpgrade(root); err == nil {
				t.Fatal("private native state must prevent upstream public downloads")
			}
		})
	}
}

func TestUnmanagedLegacyInstallationKeepsExistingUpgradePath(t *testing.T) {
	if err := rejectPrivateUpgrade(t.TempDir()); err != nil {
		t.Fatal(err)
	}
}
