#!/usr/bin/env python3
"""A beautifier for Bash shell scripts written in Python."""
import argparse
import difflib
import os
import re
import sys

import pkg_resources  # part of setuptools
from colorama import Fore

# correct function style detection is obtained only if following regex are
# tested in sequence.  styles are listed as follows:
# 0) function keyword, open/closed parentheses, e.g.      function foo()
# 1) function keyword, NO open/closed parentheses, e.g.   function foo
# 2) NO function keyword, open/closed parentheses, e.g.   foo()
FUNCTION_STYLE_REGEX = [
    r"\bfunction\s+(\w*)\s*\(\s*\)\s*",
    r"\bfunction\s+(\w*)\s*",
    r"\b\s*(\w*)\s*\(\s*\)\s*",
]

FUNCTION_STYLE_REPLACEMENT = [r"function \g<1>() ", r"function \g<1> ", r"\g<1>() "]

errors = {}

def main():
    """Call the main function."""
    Beautify().main()

class Beautify:
    """Class to handle both module and non-module calls."""

    def __init__(self):
        """Set tab as space and it's value to 4."""
        self.tab_str = " "
        self.tab_size = 4
        self.backup = False
        self.check_only = False
        self.apply_function_style = None  # default is no change based on function style
        self.color = True
        self.argument_order = False
        self.function_order = False
        self.variable_order = False
        self.english = False
        self.line_end = False
        self.exit_code_check = False

    def read_file(self, fp):
        """Read input file."""
        with open(fp) as f:
            return f.read()

    def write_file(self, fp, data):
        """Write output to a file."""
        with open(fp, "w", newline="\n") as f:
            f.write(data)

    def detect_function_style(self, test_record):
        """Returns the index for the function declaration style detected in the given string
        or None if no function declarations are detected."""
        index = 0
        # IMPORTANT: apply regex sequentially and stop on the first match:
        for regex in FUNCTION_STYLE_REGEX:
            if re.search(regex, test_record):
                return index
            index += 1
        return None

    def change_function_style(self, stripped_record, func_decl_style):
        """Converts a function definition syntax from the 'func_decl_style' to
        the one that has been set in self.apply_function_style and returns the
        string with the converted syntax."""
        if func_decl_style is None:
            return stripped_record
        if self.apply_function_style is None:
            # user does not want to enforce any specific function style
            return stripped_record
        regex = FUNCTION_STYLE_REGEX[func_decl_style]
        replacement = FUNCTION_STYLE_REPLACEMENT[self.apply_function_style]
        changed_record = re.sub(regex, replacement, stripped_record)
        return changed_record.strip()

    def get_test_record(self, source_line):
        """Takes the given Bash source code line and simplifies it by removing stuff that is not
        useful for the purpose of indentation level calculation"""
        # first of all, get rid of escaped special characters like single/double quotes
        # that may impact later "collapse" attempts
        test_record = source_line.replace("\\'", "")
        test_record = test_record.replace('\\"', "")

        # collapse multiple quotes between ' ... '
        test_record = re.sub(r"\'.*?\'", "", test_record)
        # collapse multiple quotes between " ... "
        test_record = re.sub(r'".*?"', "", test_record)
        # collapse multiple quotes between ` ... `
        test_record = re.sub(r"`.*?`", "", test_record)
        # collapse multiple quotes between \` ... ' (weird case)
        test_record = re.sub(r"\\`.*?\'", "", test_record)
        # strip out any escaped single characters
        test_record = re.sub(r"\\.", "", test_record)
        # remove '#' comments
        test_record = re.sub(r"(\A|\s)(#.*)", "", test_record, 1)
        return test_record

    def beautify_string(self, data, path=""):
        """Beautify string (file part)."""
        tab = 0
        case_level = 0
        prev_line_had_continue = False
        continue_line = False
        started_multiline_quoted_string = False
        ended_multiline_quoted_string = False
        open_brackets = 0
        in_here_doc = False
        defer_ext_quote = False
        in_ext_quote = False
        ext_quote_string = ""
        here_string = ""
        output = []
        line = 1
        formatter = True
        for record in re.split("\n", data):
            record = record.rstrip()
            stripped_record = record.strip()
            continued_if = False

            if (re.search(r'.*&{1,2}|.*\|{1,2}$', record)):
                continued_if = True

            # preserve blank lines
            if not stripped_record:
                output.append(stripped_record)
                continue

            # ensure space before ;; terminators in case statements
            if case_level:
                stripped_record = re.sub(r"(\S);;", r"\1 ;;", stripped_record)

            test_record = self.get_test_record(stripped_record)

            # detect whether this line ends with line continuation character:
            prev_line_had_continue = continue_line
            continue_line = True if (re.search(r"\\$", stripped_record) is not None) else False
            inside_multiline_quoted_string = (
                prev_line_had_continue and continue_line and started_multiline_quoted_string
            )

            if not continue_line and prev_line_had_continue and started_multiline_quoted_string:
                # remove contents of strings initiated on previous lines and
                # that are ending on this line:
                [test_record, num_subs] = re.subn(r'^[^"]*"', "", test_record)
                ended_multiline_quoted_string = True if num_subs > 0 else False
            else:
                ended_multiline_quoted_string = False

            if (
                (in_here_doc)
                or (inside_multiline_quoted_string)
                or (ended_multiline_quoted_string)
            ):  # pass on with no changes
                output.append(record)
                # now test for here-doc termination string
                if re.search(here_string, test_record) and not re.search(r"<<", test_record):
                    in_here_doc = False
            else:  # not in here doc or inside multiline-quoted
                if continue_line:
                    if prev_line_had_continue:
                        # this line is not STARTING a multiline-quoted string...
                        # we may be in the middle of such a multiline string
                        # though
                        started_multiline_quoted_string = False
                    else:
                        # remove contents of strings initiated on current line
                        # but that continue on next line (in particular we need
                        # to ignore brackets they may contain!)
                        [test_record, num_subs] = re.subn(r'"[^"]*?\\$', "", test_record)
                        started_multiline_quoted_string = True if num_subs > 0 else False
                else:
                    # this line is not STARTING a multiline-quoted string
                    started_multiline_quoted_string = False

                if (re.search(r"<<-?", test_record)) and not (re.search(r".*<<<", test_record)):
                    here_string = re.sub(
                        r'.*<<-?\s*[\'|"]?([_|\w]+)[\'|"]?.*', r"\1", stripped_record, 1
                    )
                    in_here_doc = len(here_string) > 0

                if in_ext_quote:
                    if re.search(ext_quote_string, test_record):
                        # provide line after quotes
                        test_record = re.sub(r".*%s(.*)" % ext_quote_string, r"\1", test_record, 1)
                        in_ext_quote = False
                else:  # not in ext quote
                    if re.search(r'(\A|\s)(\'|")', test_record):
                        # apply only after this line has been processed
                        defer_ext_quote = True
                        ext_quote_string = re.sub(r'.*([\'"]).*', r"\1", test_record, 1)
                        # provide line before quote
                        test_record = re.sub(r"(.*)%s.*" % ext_quote_string, r"\1", test_record, 1)
                if in_ext_quote or not formatter:
                    # pass on unchanged
                    output.append(record)
                    if re.search(r"#\s*@formatter:on", stripped_record):
                        formatter = True
                        continue
                else:  # not in ext quote
                    if re.search(r"#\s*@formatter:off", stripped_record):
                        formatter = False
                        output.append(record)
                        continue

                    # multi-line conditions are often meticulously formatted
                    if open_brackets or continued_if:
                        output.append(record)
                    else:
                        inc = len(re.findall(r"(\s|\A|;)(case|then|do)(;|\Z|\s)", test_record))
                        inc += len(re.findall(r"(\{|\(|\[)", test_record))
                        outc = len(
                            re.findall(
                                r"(\s|\A|;)(esac|fi|done|elif)(;|\)|\||\Z|\s)",
                                test_record,
                            )
                        )
                        outc += len(re.findall(r"(\}|\)|\])", test_record))
                        if re.search(r"\besac\b", test_record):
                            if case_level == 0:
                                sys.stderr.write(
                                    'File %s: error: "esac" before "case" in '
                                    "line %d.\n" % (path, line)
                                )
                            else:
                                outc += 1
                                case_level -= 1

                        # special handling for bad syntax within case ... esac
                        if re.search(r"\bcase\b", test_record):
                            inc += 1
                            case_level += 1

                        choice_case = 0
                        if case_level:
                            if re.search(r"\A[^(]*\)", test_record):
                                inc += 1
                                choice_case = -1

                        # detect functions
                        func_decl_style = self.detect_function_style(test_record)
                        if func_decl_style is not None:
                            stripped_record = self.change_function_style(
                                stripped_record, func_decl_style
                            )

                        # an ad-hoc solution for the "else" or "elif ... then" keywords
                        else_case = (0, -1)[
                            re.search(r"^(else|elif\s.*?;\s+?then)", test_record) is not None
                        ]

                        net = inc - outc
                        tab += min(net, 0)

                        # while 'tab' idasdas and is used for some adjustments:
                        extab = tab + else_case + choice_case
                        if (
                            prev_line_had_continue
                            and not open_brackets
                            and not ended_multiline_quoted_string
                        ):
                            extab += 1

                        extab = max(0, extab)
                        output.append((self.tab_str * self.tab_size * extab) + stripped_record)
                        tab += max(net, 0)

                if defer_ext_quote:
                    in_ext_quote = True
                    defer_ext_quote = False

                # count open brackets for line continuation
                open_brackets += len(re.findall(r"\[", test_record))
                open_brackets -= len(re.findall(r"\]", test_record))
            line += 1
        error = tab != 0
        if error:
            sys.stderr.write("File %s: error: indent/outdent mismatch: %d.\n" % (path, tab))
        return "\n".join(output), error

    def reorder_arguments(self, regexp, value, black_list):
        """Changes the Argument order to be in alphabetical."""
        splits = value.split(' ')
        splits_length = len(splits)
        arguments={}
        first_index = 0

        # If the substring matches the regex, it means that the string contains at least one argument
        if regexp.search(value):
            for j in range(1, splits_length):
                # If the split matches the regex, then it's an argument
                if regexp.search(" %s"%(splits[j])):
                    # Sometimes there are function letters, which need to be addressed
                    if(first_index == 0):
                        first_index = j
                    # If the next split contains any of the blacklisted character or is an argument, then no value pair required
                    if(j + 1 == splits_length or (splits[j + 1][:1] != '"' and splits[j + 1][:2] != "\\\"" and (any(c in black_list for c in splits[j + 1]) or regexp.search(" %s"%(splits[j + 1]))))):
                        arguments[splits[j]] = ""
                        # Increment the last index, so we know where was the last argument
                        last_index = j
                    else:
                        arguments[splits[j]] = " %s"%(splits[j + 1])
                        # Increment the last index, so we know where was the last argument
                        last_index = j + 1
            # The first split will always be the command/script call
            new_substring = splits[0]
            # Adding the function letters back to the beginning of the line
            for j in range(1, first_index):
                new_substring += " %s"%(splits[j])
            # Ordering the selected arguments by ABC and putting them in with their value pairs
            for ordered_agrument in sorted([re.sub(r'^-*', '', w) for w in arguments]):
                if "-%s"%(ordered_agrument) in arguments:
                    ordered_agrument = "-%s"%(ordered_agrument)
                elif "--%s"%(ordered_agrument) in arguments:
                    ordered_agrument = "--%s"%(ordered_agrument)
                new_substring += " %s%s"%(ordered_agrument,arguments[ordered_agrument])
            # The range between the last argument index and the length of the split is the non argument values
            for j in range(last_index + 1, len(splits)):
                new_substring += " %s"%(splits[j])
        # Else the substring does not contain any argument, thus can be reconstructed without any modification
        else:
            new_substring = splits[0]
            for j in range(1, splits_length):
                new_substring += " %s"%(splits[j])

        return new_substring

    def change_argument_order(self, data, path):
        """Checks if the argument calls are in ABC order in the line. If not, then change the order."""
        regexp = re.compile(r' -{1,2}[a-zA-Z]+')
        black_list = "|,&;"

        for line in data.split('\n'):
            # If the line contains the [ character, then it's most likely an If statement, which could give us false positive
            # eval lines are currently not supported
            if not "[" in line and not "eval" in line and regexp.search(line): 
                subshells = {}
                new_line = line

                # Collecting the subshells
                for a in (re.findall(' -{1,2}[a-zA-Z] \$\(.*\)', line)):
                    for b in a.split(")"):
                        if("" != b):
                            b = b + ")"
                            keys = re.findall('(-{1,2}[a-zA-Z]) \$', b)
                            values = re.findall('\$\(.*\)', b)

                            for i in range(len(keys)):
                                subshells[keys[i]] = values[i]
                
                # Deleting subshells from the line
                for value in subshells.values():
                    new_line = new_line.replace(value, "")

                for key in subshells.keys():
                    value = str(subshells[key])[2:-1]
                    subshells[key] = self.reorder_arguments(regexp, value, black_list)

                # Spliting the line into substrings by pipe or & characters
                substrings = re.split(' [|&]{1,2} ',new_line)
                # Saving the | and & characters for reconstruction
                pipes = re.findall(' [|&]{1,2} ',new_line)

                for substring_index in range(len(substrings)):
                    new_substring = self.reorder_arguments(regexp, substrings[substring_index], black_list)
                    
                    # If this is the first substring, then the new_line value will be overridden
                    if substring_index == 0:
                        new_line = new_substring
                    # Else we concat it with the pipe and the new substring
                    else:
                        new_line += pipes[substring_index -1] + new_substring

                
                for key in subshells.keys():
                    new_line = new_line.replace(key, "%s $(%s)"%(key, subshells[key]))

                new_line = re.sub(r'\s+$', '', new_line.replace(")  ", ") "))
                if line != new_line:
                    errors[path] += "The file contains argument order issue(s).\n"
                        
                # Replacing the old line with the new
                data = data.replace(line, new_line)

        return data

    def change_function_order(self, data, path):
        """Checks if the functions are in ABC order. If not, then change the order."""
        regexp = re.compile(r'function.*')
        if regexp.search(data):
            lines = data.split('\n')
            functions={}
            line_number=0
            start_line_number = -1

            for line in lines:
                # If the line matches the regex, then we consider it as a function start
                if regexp.search(line):
                    # Saving the function name and the line it's on
                    start_line = line.replace(" {", "").replace("function ", "")
                    start_line_number = line_number

                # If there is already a function definition found and the line only consist of }, the script assumes that this is the end of the function
                if start_line_number > -1 and line == "}":
                    # Saving the function name (key) and the start and stop line numbers (value)
                    functions[start_line] = "%s;%s"%(start_line_number,line_number)
                    start_line_number = -1

                line_number += 1
            
            new_data = ""
            # Adding all the lines before the first function declaration to the new_data string.
            for i in range(int(functions[list(functions)[0]].split(';')[0])):
                new_data += "%s\n"%(lines[i])

            # Adding the ordered functions to the new_data string.
            sorted_functions = sorted(functions)
            sorted_functions_length = len(sorted_functions)
            for i in range(sorted_functions_length):
                split = functions[sorted_functions[i]].split(';')
                for j in range(int(split[0]), int(split[1]) + 1):
                    line_end = ""
                    if lines[j] != "}":
                        line_end = "\n"
                    new_data += "%s%s"%(lines[j],line_end)
                if i < sorted_functions_length - 1:
                    new_data += "\n\n"

            # Adding all the lines after the last function declaration to the new_data string.
            for i in range(int(functions[list(functions)[len(functions) -1]].split(';')[1]) + 1, len(lines)):
                new_data += "%s"%(lines[i])

            if new_data != data:
                errors[path] += "The file contains function order issue(s).\n"

            return new_data
        else:
            return data
    
    def check_line_break_before_exit_code(self, data, path):
        """Checks if there is any empty line before the exit command"""
        lines = data.split('\n')
        changed = False 
        regexp = re.compile(r'^\s*exit [0-9]*')

        for i in range(len(lines)):
            if regexp.search(lines[i]) and lines[i - 1] != "" and "function" not in lines[i - 1] and "then" != lines[i - 1] and "do" != lines[i - 1]:
                lines[i] = "\n%s"%(lines[i])
                       
        if changed:
            errors[path] += "The file contains exit commands without empty line before them.\n"

        return "\n".join(lines)

    def check_variable_order(self, data, path):
        """Checks if the variables are in order"""
        lines = data.split('\n')
        changed = False
        regexp = re.compile(r'^\s+[a-zA-z]+={1}[^=].*')
        regexp1 = re.compile(r'^\s+local\s+[a-zA-z]+={1}[^=].*')
        regexp2 = re.compile(r'^\s+export\s+[a-zA-z]+={1}[^=].*')
        variables={}

        for i in range(len(lines)):
            line = lines[i]
            if regexp.search(line) or regexp1.search(line) or regexp2.search(line):
                variables[re.sub(r'=.*', "", line.replace(" ", "").replace("export", "").replace("local", "").replace("\t", ""))] = i
            elif len(variables) > 0:
                if(len(variables) > 1):
                    if(sorted(variables) != list(variables)):
                           for key in variables:
                                lines[variables[key]] += " -- Please change the order/name of this variable to be in ABC order."
                                changed = True
                variables.clear()
                       
        if changed:
            errors[path] += "The file contains variables in wrong order.\n"

        return "\n".join(lines)

    def check_last_line(self, data, path):
        """Checks if the last line is empty"""
        lines = data.split('\n')
        changed = False

        for i in range(len(lines) -1, 0, -1):
            if lines[i] == "":
                lines.pop(i)
                changed = True
            else:
                break
                       
        if changed:
            errors[path] += "The file contains empty last line(s)\n"

        return "\n".join(lines)

    def beautify_file(self, path):
        """Beautify bash script file."""
        error = False
        if path == "-":
            data = sys.stdin.read()
            result, error = self.beautify_string(data, "(stdin)")

            if self.argument_order:
                result = self.change_argument_order(result, path)
            if self.function_order and self.apply_function_style == 1:
                result = self.change_function_order(result, path)
            elif self.function_order and self.apply_function_style != 1:
                errors[path] += "Function ordering does not support the provided function style.\n"
                error = True
            if self.line_end:
                result = self.check_last_line(result, path)
            if self.variable_order:
                result = self.check_variable_order(result, path)
            if self.exit_code_check:
                result = self.check_line_break_before_exit_code(result, path)
            sys.stdout.write(result)
        else:  # named file
            data = self.read_file(path)
            result, error = self.beautify_string(data, path)

            if self.argument_order:
                result = self.change_argument_order(result, path)
            if self.function_order and self.apply_function_style == 1:
                result = self.change_function_order(result, path)
            elif self.function_order and self.apply_function_style != 1:
                errors[path] += "Function ordering does not support the provided function style.\n"
                error = True
            if self.line_end:
                result = self.check_last_line(result, path)
            if self.variable_order:
                result = self.check_variable_order(result, path)
            if self.exit_code_check:
                result = self.check_line_break_before_exit_code(result, path)
            if data != result:
                if self.check_only:
                    if not error:
                        # we want to return 0 (success) only if the given file is already
                        # well formatted:
                        error = result != data
                        # print out the changes
                else:
                    if self.backup:
                        self.write_file(path + ".bak", data)
                    self.write_file(path, result)
        
        if self.english:
            try:
                result.encode(encoding='utf-8').decode('ascii')
            except UnicodeDecodeError:
                errors[path] += "The file contains non English characters.\n"
                error = True

        if error:
            print(Fore.CYAN + "%s\n"%(path) + Fore.RESET)
            self.print_diff(data, result)

        return error

    def color_diff(self, diff):
        for line in diff:
            if line.startswith("+"):
                yield Fore.GREEN + line + Fore.RESET
            elif line.startswith("-"):
                yield Fore.RED + line + Fore.RESET
            elif line.startswith("^"):
                yield Fore.BLUE + line + Fore.RESET
            else:
                yield line

    def print_diff(self, original: str, formatted: str):
        original_lines = original.splitlines()
        formatted_lines = formatted.splitlines()

        delta = difflib.unified_diff(
            original_lines, formatted_lines, "original", "formatted", lineterm=""
        )
        if self.color:
            delta = self.color_diff(delta)

        print("\n".join(delta))

    def print_help(self, parser):
        parser.print_help()
        sys.stdout.write(
            "\nBash function styles that can be specified via --force-function-style are:\n"
        )
        sys.stdout.write(
            "  fnpar: function keyword, open/closed parentheses, e.g.      function foo()\n"
        )
        sys.stdout.write(
            "  fnonly: function keyword, no open/closed parentheses, e.g.  function foo\n"
        )
        sys.stdout.write("  paronly: no function keyword, open/closed parentheses, e.g. foo()\n")
        sys.stdout.write("\n")

    def parse_function_style(self, style_name):
        # map the user-provided function style to our range 0-2
        if style_name == "fnpar":
            return 0
        elif style_name == "fnonly":
            return 1
        elif style_name == "paronly":
            return 2
        return None

    def get_version(self):
        try:
            return pkg_resources.require("beautysh")[0].version
        except pkg_resources.DistributionNotFound:
            return "Not Available"

    def main(self):
        """Main beautifying function."""
        error = False
        parser = argparse.ArgumentParser(
            description="A Bash beautifier for the masses, version {}".format(self.get_version()),
            add_help=False,
        )
        parser.add_argument(
            "--indent-size",
            "-i",
            nargs=1,
            type=int,
            default=4,
            help="Sets the number of spaces to be used in " "indentation.",
        )
        parser.add_argument(
            "--backup",
            "-b",
            action="store_true",
            help="Beautysh will create a backup file in the " "same path as the original.",
        )
        parser.add_argument(
            "--check",
            "-c",
            action="store_true",
            help="Beautysh will just check the files without doing " "any in-place beautify.",
        )
        parser.add_argument(
            "--tab",
            "-t",
            action="store_true",
            help="Sets indentation to tabs instead of spaces.",
        )
        parser.add_argument(
            "--force-function-style",
            "-s",
            nargs=1,
            help="Force a specific Bash function formatting. See below for more info.",
        )
        parser.add_argument(
            "--version", "-v", action="store_true", help="Prints the version and exits."
        )
        parser.add_argument("--help", "-h", action="store_true", help="Print this help message.")
        parser.add_argument(
            "--argument-order",
            "-a",
            action="store_true",
            help="Beautysh will reorder arguments to be in ABC order." " Eval lines currently not supported.",
        )
        parser.add_argument(
            "--function-order",
            "-f",
            action="store_true",
            help="Beautysh will reorder functions to be in ABC order." " Only fnonly functions are supported.",
        )
        parser.add_argument(
            "--variable-order",
            "-o",
            action="store_true",
            help="Beautysh if variables are in ABC order." " Only fnonly functions are supported.",
        )
        parser.add_argument(
            "--english",
            "-e",
            action="store_true",
            help="Beautysh will check if the file contains non English characters.",
        )
        parser.add_argument(
            "--exit-code",
            "-x",
            action="store_true",
            help="Beautysh will check if every exit code has an empty line before them (except those which does not have a command before them).",
        )
        parser.add_argument(
            "--line-end",
            "-l",
            action="store_true",
            help="Beautysh will check if the file contains non English characters.",
        )
        parser.add_argument(
            "files",
            metavar="FILE",
            nargs="*",
            help="Files to be beautified. This is mandatory. "
            "If - is provided as filename, then beautysh reads "
            "from stdin and writes on stdout.",
        )
        args = parser.parse_args()
        if (len(sys.argv) < 2) or args.help:
            self.print_help(parser)
            exit()
        if args.version:
            sys.stdout.write("%s\n" % self.get_version())
            exit()
        if type(args.indent_size) is list:
            args.indent_size = args.indent_size[0]
        if not args.files:
            sys.stdout.write("Please provide at least one input file\n")
            exit()
        self.tab_size = args.indent_size
        self.backup = args.backup
        self.check_only = args.check
        self.argument_order = args.argument_order
        self.function_order = args.function_order
        self.english = args.english
        self.line_end = args.line_end
        self.variable_order = args.variable_order
        self.exit_code_check = args.exit_code
        if args.tab:
            self.tab_size = 1
            self.tab_str = "\t"
        if type(args.force_function_style) is list:
            provided_style = self.parse_function_style(args.force_function_style[0])
            if provided_style is None:
                sys.stdout.write("Invalid value for the function style. See --help for details.\n")
                exit()
            self.apply_function_style = provided_style
        if "NO_COLOR" in os.environ:
            self.color = False
        for path in args.files:
            errors[path] = ""
            error |= self.beautify_file(path)

        if  args.check:
            print("\nSummary:")
            if error:
                for file in sorted(errors):
                    if errors[file] != "":
                        print(Fore.CYAN + "  %s:"%(file) + Fore.RESET)
                        for file_error in errors[file].split('\n'):
                            if file_error != "":
                                print(Fore.RED + "    - %s"%(file_error) + Fore.RESET)
            else:
                print(Fore.GREEN + "No error found." + Fore.RESET)

        sys.exit((0, 1)[error])


# if not called as a module
if __name__ == "__main__":
    Beautify().main()
