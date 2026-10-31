// ExportFunction.java — Ghidra headless script for the Route B experiment.
//
// Usage (through ghidra/export.sh):
//   analyzeHeadless <proj> <name> -import <elf> -postScript ExportFunction.java \
//       copy_name /abs/path/out/copy_name.c
//
// Writes:
//   <out>.c        raw decompiler output (never edit this file in place)
//   <out>.meta.txt entry, size, signature, calling convention, callees
//
// The raw output is evidence. Normalization happens in a separate file so the
// original recovery and every manual correction remain reviewable.

import java.io.File;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.util.Set;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;

public class ExportFunction extends GhidraScript {

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("usage: ExportFunction.java <function-name> <output.c>");
            return;
        }

        String targetName = args[0];
        File outC = new File(args[1]);
        File outMeta = new File(args[1] + ".meta.txt");

        Function target = null;
        FunctionIterator it = currentProgram.getFunctionManager().getFunctions(true);
        while (it.hasNext()) {
            Function f = it.next();
            if (f.getName().equals(targetName)) {
                target = f;
                break;
            }
        }

        if (target == null) {
            println("NOT FOUND: " + targetName);
            println("Re-run with a name from: analyzeHeadless ... -postScript ListFunctions.java");
            return;
        }

        DecompInterface ifc = new DecompInterface();
        ifc.toggleCCode(true);
        ifc.toggleSyntaxTree(true);
        if (!ifc.openProgram(currentProgram)) {
            println("DECOMPILER: failed to open program");
            return;
        }

        DecompileResults results = ifc.decompileFunction(target, 120, monitor);
        if (!results.decompileCompleted()) {
            println("DECOMPILE FAILED: " + results.getErrorMessage());
            return;
        }

        String c = results.getDecompiledFunction().getC();

        try (PrintWriter pw = new PrintWriter(outC, StandardCharsets.UTF_8)) {
            pw.print(c);
        }

        StringBuilder meta = new StringBuilder();
        meta.append("name=").append(target.getName()).append('\n');
        meta.append("entry=").append(target.getEntryPoint()).append('\n');
        meta.append("size_bytes=").append(target.getBody().getNumAddresses()).append('\n');
        meta.append("signature=").append(target.getSignature()).append('\n');
        meta.append("calling_convention=").append(target.getCallingConventionName()).append('\n');
        meta.append("external=").append(target.isExternal()).append('\n');
        meta.append("thunk=").append(target.isThunk()).append('\n');
        meta.append("callees=");
        Set<Function> callees = target.getCalledFunctions(monitor);
        for (Function callee : callees) {
            meta.append(callee.getName()).append(' ');
        }
        meta.append('\n');

        try (PrintWriter pw = new PrintWriter(outMeta, StandardCharsets.UTF_8)) {
            pw.print(meta);
        }

        println("WROTE " + outC.getAbsolutePath() + " (" + c.length() + " chars)");
        println("WROTE " + outMeta.getAbsolutePath());
        println("SIZE " + target.getBody().getNumAddresses() + " bytes at " + target.getEntryPoint());
    }
}
