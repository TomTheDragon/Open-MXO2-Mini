// Decompile every function of the current program into one C file.
// Usage (headless): -postScript DecompileAll.java <output.c>
//@category MXO2

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;

import java.io.FileWriter;
import java.io.PrintWriter;

public class DecompileAll extends GhidraScript {
	@Override
	protected void run() throws Exception {
		String out = getScriptArgs().length > 0 ? getScriptArgs()[0] : "/tmp/out.c";
		DecompInterface d = new DecompInterface();
		d.openProgram(currentProgram);
		try (PrintWriter w = new PrintWriter(new FileWriter(out))) {
			for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
				if (monitor.isCancelled())
					break;
				DecompileResults r = d.decompileFunction(f, 60, monitor);
				w.println("// ==== " + f.getName() + " @ " + f.getEntryPoint());
				if (r != null && r.decompileCompleted())
					w.println(r.getDecompiledFunction().getC());
				else
					w.println("// decompile failed\n");
			}
		}
		d.dispose();
	}
}
