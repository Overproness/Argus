package main

import (
	"context"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.24.0"
	"go.opentelemetry.io/otel/trace"
)

func main() {
	ctx := context.Background()
	// No explicit endpoint option: the standard OTEL_EXPORTER_OTLP_(TRACES_)ENDPOINT env vars drive it,
	// same as a real Go service configured only through environment variables.
	exp, err := otlptracehttp.New(ctx)
	if err != nil {
		panic(err)
	}
	res, _ := resource.New(ctx, resource.WithAttributes(semconv.ServiceName("interop-go")))
	tp := sdktrace.NewTracerProvider(sdktrace.WithBatcher(exp, sdktrace.WithBatchTimeout(200*time.Millisecond)),
		sdktrace.WithResource(res))
	otel.SetTracerProvider(tp)
	tracer := tp.Tracer("argus-test")

	ctx, outer := tracer.Start(ctx, "outer", trace.WithAttributes(
		attribute.String("code.function.name", "svc.api.handler"),
		attribute.Int("items.count", 3),
	))
	_, client := tracer.Start(ctx, "GET", trace.WithSpanKind(trace.SpanKindClient), trace.WithAttributes(
		attribute.String("http.request.method", "GET"),
		attribute.String("url.full", "http://127.0.0.1:9/x"),
	))
	client.SetStatus(codes.Error, "connection refused")
	client.End()
	outer.End()
	if err := tp.ForceFlush(ctx); err != nil {
		panic(err)
	}
	if err := tp.Shutdown(ctx); err != nil {
		panic(err)
	}
}
