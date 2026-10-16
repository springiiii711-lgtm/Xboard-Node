// Parse workflow YAML without starting any application or listening on a port.
package main

import (
    "fmt"
    "os"
    "gopkg.in/yaml.v3"
)

func main() {
    if len(os.Args) < 2 {
        fmt.Fprintln(os.Stderr, "usage: go run ci/check-workflow.go workflow.yml ...")
        os.Exit(2)
    }
    for _, path := range os.Args[1:] {
        contents, err := os.ReadFile(path)
        if err != nil { panic(err) }
        var workflow map[string]any
        if err := yaml.Unmarshal(contents, &workflow); err != nil { panic(err) }
        if workflow["name"] == nil || workflow["on"] == nil || workflow["jobs"] == nil {
            panic("missing workflow name, triggers, or jobs: " + path)
        }
        fmt.Printf("YAML parsed: %s\n", path)
    }
}
