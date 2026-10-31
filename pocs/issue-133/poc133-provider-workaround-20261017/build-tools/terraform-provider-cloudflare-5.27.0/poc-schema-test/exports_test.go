package poc_test

import (
    "context"
    "testing"

    "github.com/cloudflare/terraform-provider-cloudflare/internal/services/worker_version"
    "github.com/hashicorp/terraform-plugin-framework/resource/schema"
    "github.com/hashicorp/terraform-plugin-framework/types"
    "github.com/hashicorp/terraform-plugin-framework/types/basetypes"
)

func TestExportsRoundTrip(t *testing.T) {
    ctx := context.Background()
    s := worker_version.ResourceSchema(ctx)
    exports := s.Attributes["exports"].(schema.MapNestedAttribute)
    objectType := exports.NestedObject.Type().(types.ObjectType)
    for _, kind := range []string{"worker", "durable-object"} {
        t.Run(kind, func(t *testing.T) {
            model := worker_version.WorkerVersionExportsModel{
                Type: types.StringValue(kind), State: types.StringValue("created"),
                Container: types.StringNull(), Storage: types.StringNull(),
                RenamedTo: types.StringNull(), TransferredTo: types.StringNull(), TransferFrom: types.StringNull(),
            }
            if kind == "durable-object" { model.Storage = types.StringValue("sqlite") }
            value, diagnostics := types.ObjectValueFrom(ctx, objectType.AttributeTypes(), model)
            if diagnostics.HasError() { t.Fatalf("model to schema: %s", diagnostics) }
            var restored worker_version.WorkerVersionExportsModel
            diagnostics = value.As(ctx, &restored, basetypes.ObjectAsOptions{})
            if diagnostics.HasError() { t.Fatalf("schema to model: %s", diagnostics) }
            if !restored.Container.IsNull() || restored.Type.ValueString() != kind || restored.State.ValueString() != "created" {
                t.Fatal("export round-trip changed the value")
            }
        })
    }
}
