using Mono.Cecil;
using Mono.Cecil.Cil;
using System.Text.RegularExpressions;

// 用法：
//   ildump <assembly> <Type::Method> [<Type::Method> ...]   —— 逐条 IL（字面量已解析）
//   ildump <assembly> --grep <regex>                        —— 按名字查声明与引用
// 加 --grep 的原因：反编译只落了 oracle/og/ob/ss 四个类，HaloFlare、FirstHaloSprite
// 这类成员属于别的类型，得反查“谁在用”才会现形。
string asmPath = args[0];
var module = ModuleDefinition.ReadModule(asmPath);

if (args.Length >= 3 && args[1] == "--grep")
{
    var re = new Regex(args[2], RegexOptions.CultureInvariant);
    Console.WriteLine($"##### 声明（类型/属性/字段/方法名匹配 {args[2]}）");
    foreach (var type in AllTypes(module.Types))
    {
        if (re.IsMatch(type.Name)) Console.WriteLine($"TYPE   {type.FullName}");
        foreach (var p in type.Properties)
            if (re.IsMatch(p.Name)) Console.WriteLine($"PROP   {type.FullName}::{p.Name} : {p.PropertyType.Name}");
        foreach (var f in type.Fields)
            if (re.IsMatch(f.Name)) Console.WriteLine($"FIELD  {type.FullName}::{f.Name} : {f.FieldType.Name}");
        foreach (var m in type.Methods)
            if (re.IsMatch(m.Name)) Console.WriteLine($"METHOD {type.FullName}::{m.Name}");
    }

    Console.WriteLine($"##### 引用（方法体内出现同名操作数）");
    foreach (var type in AllTypes(module.Types))
        foreach (var m in type.Methods)
        {
            if (!m.HasBody) continue;
            foreach (var ins in m.Body.Instructions)
            {
                string name = ins.Operand switch
                {
                    MemberReference mr => mr.Name,
                    CallSite cs => cs.Name,
                    _ => null,
                };
                if (name != null && re.IsMatch(name))
                    Console.WriteLine($"{type.FullName}::{m.Name}  IL_{ins.Offset:x4}  {ins.OpCode} {ins.Operand}");
            }
        }
    return;
}

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

static IEnumerable<TypeDefinition> AllTypes(IEnumerable<TypeDefinition> types)
{
    foreach (var t in types)
    {
        yield return t;
        foreach (var n in AllTypes(t.NestedTypes)) yield return n;
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
