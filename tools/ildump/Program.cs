using Mono.Cecil;
using Mono.Cecil.Cil;

// usage: ildump <assembly> <Type::Method> [<Type::Method> ...]
// dumps IL with resolved literals so the arithmetic is readable by hand.
string asmPath = args[0];
var module = ModuleDefinition.ReadModule(asmPath);

foreach (string spec in args.Skip(1))
{
    int sep = spec.IndexOf("::", StringComparison.Ordinal);
    string typeName = spec[..sep];
    string methodName = spec[(sep + 2)..];

    var type = module.Types.FirstOrDefault(t => t.FullName == typeName)
               ?? Search(module.Types, typeName);
    if (type == null) { Console.WriteLine($"!! type not found: {typeName}"); continue; }

    var methods = type.Methods.Where(m => m.Name == methodName || m.Name.StartsWith(methodName + "(")).ToList();
    if (methods.Count == 0) { Console.WriteLine($"!! method not found: {spec}"); continue; }

    foreach (var method in methods)
    {
        Console.WriteLine($"===== {type.FullName}::{method.Name}({string.Join(", ", method.Parameters.Select(p => p.ParameterType.Name + " " + p.Name))}) =====");
        if (!method.HasBody) { Console.WriteLine("   <no body>"); continue; }
        foreach (var v in method.Body.Variables)
            Console.WriteLine($"   local {v.Index}: {v.VariableType.Name}");
        foreach (var ins in method.Body.Instructions)
            Console.WriteLine($"   {ins}");
        Console.WriteLine();
    }
}

static TypeDefinition Search(IEnumerable<TypeDefinition> types, string name)
{
    foreach (var t in types)
    {
        if (t.Name == name || t.FullName == name) return t;
        var nested = Search(t.NestedTypes, name);
        if (nested != null) return nested;
    }
    return null;
}
