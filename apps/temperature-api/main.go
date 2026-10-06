package main

import (
	"encoding/json"
	"log"
	"math/rand/v2"
	"net/http"
	"time"
)

func temperature(w http.ResponseWriter, r *http.Request) {
	location := r.URL.Query().Get("location")
	sensorID := r.PathValue("sensor_id")
	if sensorID == "" {
		sensorID = r.URL.Query().Get("sensor_id")
	}

	// If no location is provided, use a default based on sensor ID
	if location == "" {
		switch sensorID {
		case "1":
			location = "Living Room"
		case "2":
			location = "Bedroom"
		case "3":
			location = "Kitchen"
		default:
			location = "Unknown"
		}
	}

	// If no sensor ID is provided, generate one based on location
	if sensorID == "" {
		switch location {
		case "Living Room":
			sensorID = "1"
		case "Bedroom":
			sensorID = "2"
		case "Kitchen":
			sensorID = "3"
		default:
			sensorID = "0"
		}
	}

	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	json.NewEncoder(w).Encode(map[string]any{
		"value":       15 + rand.Float64()*15,
		"unit":        "°C",
		"timestamp":   time.Now().UTC(),
		"location":    location,
		"status":      "active",
		"sensor_id":   sensorID,
		"sensor_type": "temperature",
		"description": "Simulated temperature",
	})
}

func newHandler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /temperature", temperature)
	mux.HandleFunc("GET /temperature/{sensor_id}", temperature)
	return mux
}

func main() {
	server := &http.Server{
		Addr:              ":8081",
		Handler:           newHandler(),
		ReadHeaderTimeout: 5 * time.Second,
	}
	log.Fatal(server.ListenAndServe())
}
