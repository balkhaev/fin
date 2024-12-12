"use client"

import {
  ColumnDef,
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  SortingState,
  useReactTable,
} from "@tanstack/react-table"

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Button } from "@/components/ui/button"
import { useState } from "react"
import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from "lucide-react"
import { useCopilotReadable } from "@copilotkit/react-core"
import { AppTable } from "@/lib/helpers"
import { omit } from "remeda"
import { DateTimePicker } from "@/components/ui/time-picker"
import { subDays } from "date-fns"
import { ColumnToggle } from "@/components/app/column-toggler"

interface DataTableProps {
  columns: ColumnDef<AppTable<"analysis">>[]
  sortingState?: SortingState
  data: AppTable<"analysis">[]
}

export function DataTable({
  columns,
  data,
  sortingState = [],
}: DataTableProps) {
  const [sorting, setSorting] = useState<SortingState>(sortingState)
  const [pagination, setPagination] = useState({
    pageIndex: 0,
    pageSize: 20,
  })
  const [dateFrom, setDateFrom] = useState<Date>(subDays(new Date(), 1))
  const [dateTo, setDateTo] = useState<Date>()

  const table = useReactTable<AppTable<"analysis">>({
    data,
    columns,
    state: {
      sorting,
      pagination,
    },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getSortedRowModel: getSortedRowModel(),
    onPaginationChange: setPagination,
    filterFns: {
      dateRange: (row, columnId, value) => {
        const cellValue = row.getValue(columnId) as string
        const date = new Date(cellValue)
        return (
          (!value.from || date >= value.from) && (!value.to || date <= value.to)
        )
      },
    },
    // @ts-expect-error asd
    globalFilterFn: "dateRange",
  })

  useCopilotReadable({
    description: "Это результаты технического анализа валютных пар",
    value: table
      .getPaginationRowModel()
      .rows.map((row) => omit(row.original, ["rating", "signals", "trend"])),
  })

  return (
    <div className="flex flex-col overflow-hidden flex-1 [&>*:second-child]:flex-1 [&>*+*]:border-t-2">
      <div className="flex [&>*+*]:border-l-2 [&>:last-child]:border-r-2">
        <div>
          <DateTimePicker date={dateFrom} onChange={setDateFrom} label="From" />
        </div>
        <div>
          <DateTimePicker date={dateTo} onChange={setDateTo} label="To" />
        </div>
        <div>
          <ColumnToggle table={table} />
        </div>
      </div>
      <Table>
        <TableHeader className="sticky top-0 bg-background">
          {table.getHeaderGroups().map((headerGroup) => (
            <TableRow key={headerGroup.id}>
              {headerGroup.headers.map((header) => {
                return (
                  <TableHead key={header.id}>
                    {header.isPlaceholder ? null : (
                      <div className="flex items-center gap-2">
                        {flexRender(
                          header.column.columnDef.header,
                          header.getContext()
                        )}
                        {header.column.getCanSort() && (
                          <button
                            onClick={header.column.getToggleSortingHandler()}
                            className="text-xs text-gray-500 hover:text-gray-700"
                          >
                            {header.column.getIsSorted() === "asc" && "▲"}
                            {header.column.getIsSorted() === "desc" && "▼"}
                            {!header.column.getIsSorted() && "⇅"}
                          </button>
                        )}
                      </div>
                    )}
                  </TableHead>
                )
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows?.length ? (
            table.getRowModel().rows.map((row) => (
              <TableRow
                key={row.id}
                data-state={row.getIsSelected() && "selected"}
              >
                {row.getVisibleCells().map((cell) => (
                  <TableCell key={cell.id}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))
          ) : (
            <TableRow>
              <TableCell colSpan={columns.length} className="h-24 text-center">
                Нет результатов
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
      <div className="flex items-center justify-between px-2 py-4">
        <div className="flex-1 text-sm text-muted-foreground">
          Страница {table.getState().pagination.pageIndex + 1} из{" "}
          {table.getPageCount()}
        </div>
        <div className="flex items-center space-x-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => table.setPageIndex(0)}
            disabled={!table.getCanPreviousPage()}
          >
            <ChevronsLeft className="h-4 w-4" />
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => table.previousPage()}
            disabled={!table.getCanPreviousPage()}
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => table.nextPage()}
            disabled={!table.getCanNextPage()}
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => table.setPageIndex(table.getPageCount() - 1)}
            disabled={!table.getCanNextPage()}
          >
            <ChevronsRight className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}
