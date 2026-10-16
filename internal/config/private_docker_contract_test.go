package config

import "testing"

// Parse the Python installer's shared synthetic fixture with the actual runtime
// loader. This starts no services and makes no network requests.
func TestPrivateDockerInstallerContract(t *testing.T) {
	for _, name := range []string{"apiHost", "API_HOST", "apiKey", "API_KEY", "nodeID", "NODE_ID", "MACHINE_ID", "MACHINE_TOKEN", "kernel", "KERNEL_TYPE", "domain", "DOMAIN", "certFile", "CERT_FILE", "keyFile", "KEY_FILE"} {
		t.Setenv(name, "")
	}
	root, err := LoadRoot("../../ops/private-node/tests/fixtures/config.json")
	if err != nil {
		t.Fatal(err)
	}
	instances, err := root.NormalizeInstances()
	if err != nil || len(instances) != 1 {
		t.Fatalf("expected one machine instance: %v", err)
	}
	cfg := instances[0]
	if !cfg.IsMachineMode() || cfg.Machine.MachineID != 42 || cfg.Machine.Token != "SYNTHETIC_MACHINE_TOKEN_0123456789" {
		t.Fatal("machine binding/credential contract was not preserved")
	}
	if cfg.Panel.URL != "https://panel.example.test" || cfg.Kernel.ConfigDir != "/var/lib/xboard-node/kernel" || cfg.Cert.CertDir != "/var/lib/xboard-node/certs" || cfg.HealthPort != 0 {
		t.Fatal("panel, writable data paths or disabled diagnostics port differ")
	}
	if err := ValidateStartupLayout(instances); err != nil {
		t.Fatal(err)
	}
}
